#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const BASE = process.env.V28_BASE || 'http://127.0.0.1:3000';
const ACTIVE = new Set(['queued', 'dispatching', 'preparing', 'running', 'rendering', 'downloading', 'downloading_images', 'waiting_flow']);
const DONE = new Set(['done', 'failed', 'partial_failed', 'interrupted', 'qc_failed']);

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

function arg(name, fallback = '') {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}
function has(name) { return process.argv.includes(name); }

function request(method, route, payload = null, timeoutMs = 20000) {
  const url = new URL(route, BASE);
  const body = payload == null ? null : Buffer.from(JSON.stringify(payload));
  return new Promise((resolve, reject) => {
    const req = http.request({
      method,
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      headers: body ? { 'content-type': 'application/json', 'content-length': body.length } : {},
      timeout: timeoutMs,
    }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`HTTP ${res.statusCode} ${method} ${route}: ${raw}`));
          return;
        }
        try { resolve(raw ? JSON.parse(raw) : null); }
        catch (error) { reject(new Error(`JSON parse failed ${method} ${route}: ${error.message}\n${raw.slice(0, 500)}`)); }
      });
    });
    req.on('timeout', () => req.destroy(new Error(`timeout ${method} ${route}`)));
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function tryRequest(method, route, payload = null, timeoutMs = 5000) {
  try { return await request(method, route, payload, timeoutMs); }
  catch { return null; }
}

async function startServerIfNeeded() {
  if (await tryRequest('GET', '/api/status', null, 3000)) return null;
  const python = fs.existsSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'))
    ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    : 'py';
  const logPath = path.join(ROOT, 'data', 'js_chain_server.log');
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  const log = fs.openSync(logPath, 'a');
  const env = { ...process.env, V28_PORT: '3000', V28_EDGE_DEBUG_PORT: '9224', V28_SUPERVISED: '1' };
  const args = python === 'py' ? ['run_server.py'] : [path.join(ROOT, 'run_server.py')];
  const proc = spawn(python, args, { cwd: ROOT, env, stdio: ['ignore', log, log], windowsHide: true });
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (proc.exitCode != null) throw new Error(`server exited early code=${proc.exitCode}; log=${logPath}`);
    if (await tryRequest('GET', '/api/status', null, 3000)) {
      console.log(`SERVER_STARTED pid=${proc.pid} log=${logPath}`);
      return proc;
    }
    await sleep(2000);
  }
  throw new Error(`server start timeout; pid=${proc.pid}; log=${logPath}`);
}

async function waitReady(timeoutSec = 180) {
  const deadline = Date.now() + timeoutSec * 1000;
  let last = null;
  while (Date.now() < deadline) {
    const status = await tryRequest('GET', '/api/status', null, 5000);
    if (status) {
      const flow = status.flow || {};
      last = flow;
      const ext = Boolean(flow.extensionConnected && flow.extensionCompatible);
      const beauty = Boolean(flow.sources && flow.sources.beauty && flow.sources.beauty.connected);
      const parenting = Boolean(flow.sources && flow.sources.parenting && flow.sources.parenting.connected);
      console.log(`READY extension=${ext} version=${flow.extension && flow.extension.version} beauty=${beauty} parenting=${parenting}`);
      if (ext && beauty) return flow;
    }
    await sleep(3000);
  }
  throw new Error(`not ready: ${JSON.stringify(last)}`);
}

function defaultPersona() {
  const candidates = [
    String.raw`D:\YT\Code\V2.5\Flow_Content_Factory_V2_15_AUTO\server\outputs\personas\minh_anh_auto\persona_master_2048.jpg`,
    path.join(ROOT, 'modules', 'flow_content', 'outputs', 'personas', 'gym_a', 'persona_master_2048.jpg'),
  ];
  const hit = candidates.find(file => fs.existsSync(file));
  if (!hit) throw new Error('persona image not found');
  return hit;
}

function baseFlow(overrides = {}) {
  return {
    imageModel: 'Nano Banana 2',
    videoModel: 'NONE',
    imageConcurrency: 1,
    videoConcurrency: 0,
    submitPolicy: 'IMAGE_ONLY',
    autoDownloadVideo: false,
    maxSubmitsPerMinute: 2,
    submitGapMs: 1200,
    aspectRatio: '9:16',
    imageOutputs: 'x1',
    videoDuration: '8s',
    videoOutputs: 'x1',
    videoExtendFactor: 'x1',
    imageTimeoutSec: 300,
    videoTimeoutSec: 900,
    ...overrides,
  };
}

function scene({ sceneId = 1, persona, makeVideo = false, mini = false }) {
  return {
    sceneId,
    imagePrompt: 'Photorealistic adult woman, age 21+, same exact identity as reference. Vietnam cafe street, natural smartphone photo, full body, vertical 9:16, realistic anatomy, no text, no watermark.',
    videoPrompt: makeVideo
      ? 'Use the attached generated image as the exact first frame. The woman gently turns toward camera, natural small smile, subtle hair and cloth motion, smooth push-in camera. Keep exact identity, outfit, lighting, background. No text, no morphing.'
      : '',
    inputImages: [{ path: persona, name: 'js_persona_front', role: 'persona_front' }],
    metadata: {
      factoryV2: { mode: makeVideo ? 'IMAGE_TO_VIDEO' : 'IMAGE_BEAT', expectedCount: 1, beatDurationSec: 8 },
      mode: makeVideo ? 'IMAGE_TO_VIDEO' : 'IMAGE_BEAT',
      makeVideo,
      mixedMotion: makeVideo,
      sceneVideoPolicy: 'PER_SCENE_V2',
      sceneMediaMode: makeVideo ? 'IMAGE_VIDEO' : 'IMAGE_ONLY',
      miniAttachTest: mini,
      jsFunctionChainTest: true,
    },
  };
}

async function createFlowJob(kind, scenes, flow) {
  const res = await request('POST', '/engine/beauty/api/flow/jobs', { kind, scenes, flow }, 20000);
  if (!res || !res.job_id) throw new Error(`no job_id: ${JSON.stringify(res)}`);
  console.log(`JOB ${res.job_id} kind=${kind} scenes=${scenes.length}`);
  return res.job_id;
}

function summarizeJob(job) {
  const rows = (job.result && job.result.results) || [];
  return rows.map(row => ({
    sceneId: row.sceneId,
    imageState: row.imageState,
    videoState: row.videoState,
    imageMediaId: row.image && row.image.mediaId,
    videoMediaIds: row.videoMediaIds || [],
    downloads: row.downloads || [],
    error: row.error || null,
  }));
}

async function pollJob(jobId, timeoutSec) {
  const deadline = Date.now() + timeoutSec * 1000;
  let last = null;
  while (Date.now() < deadline) {
    const job = await request('GET', `/engine/beauty/api/flow/jobs/${encodeURIComponent(jobId)}`, null, 10000);
    last = job;
    const rows = summarizeJob(job);
    const first = rows[0] || {};
    console.log(`POLL ${jobId} ${job.status} image=${first.imageState || '-'} video=${first.videoState || '-'} err=${String(first.error || job.error || '').slice(0, 220)}`);
    if (DONE.has(String(job.status))) return job;
    await sleep(5000);
  }
  throw new Error(`job timeout ${jobId}: ${JSON.stringify(last, null, 2).slice(0, 4000)}`);
}

function assertImageOk(job) {
  const rows = summarizeJob(job);
  const ok = rows.length && rows.every(row => row.imageState === 'SUCCESS' && row.imageMediaId && !row.error);
  if (!ok) throw new Error(`IMAGE_TEST_FAILED ${JSON.stringify({ status: job.status, error: job.error, rows }, null, 2)}`);
  return rows;
}

function assertVideoOk(job) {
  const rows = summarizeJob(job);
  const ok = rows.length && rows.every(row => row.imageState === 'SUCCESS' && row.videoState === 'SUCCESS' && row.videoMediaIds.length && !row.error);
  const hasVideoFile = rows.some(row => (row.downloads || []).some(item => item.localPath));
  const finalAssets = (job.assets || []).filter(item => item.kind === 'final_video' && item.local_path);
  const qcOk = job.status === 'done' || finalAssets.some(item => item.qc && Number(item.qc.score || 0) >= 70);
  if (!ok || !hasVideoFile || !qcOk) throw new Error(`VIDEO_CHAIN_FAILED ${JSON.stringify({ status: job.status, error: job.error, hasVideoFile, qcOk, finalAssets, rows }, null, 2)}`);
  return rows;
}

async function quickImageAttach(persona) {
  const jobId = await createFlowJob('factory_v2_mix', [scene({ persona, mini: true })], baseFlow());
  const job = await pollJob(jobId, Number(arg('--quick-timeout', '1200')));
  const rows = assertImageOk(job);
  console.log(`QUICK_IMAGE_ATTACH_OK job=${jobId} image=${rows[0].imageMediaId}`);
  return { jobId, job, rows };
}

async function linkedImageToVideo(persona) {
  const jobId = await createFlowJob('factory_v2_mix', [scene({ persona, makeVideo: true })], baseFlow({
    videoModel: 'Veo 3.1 - Fast',
    videoConcurrency: 1,
    submitPolicy: 'VIDEO_LIGHT',
    autoDownloadVideo: true,
    videoDuration: '8s',
    videoTimeoutSec: 1200,
  }));
  const job = await pollJob(jobId, Number(arg('--video-timeout', '3600')));
  const rows = assertVideoOk(job);
  console.log(`LINKED_IMAGE_TO_VIDEO_OK job=${jobId} image=${rows[0].imageMediaId} video=${rows[0].videoMediaIds.join(',')}`);
  return { jobId, job, rows };
}

async function main() {
  const keepServer = has('--keep-server');
  const persona = arg('--persona', defaultPersona());
  const server = await startServerIfNeeded();
  try {
    await waitReady(Number(arg('--ready-timeout', '180')));
    const assertJob = arg('--assert-job', '');
    if (assertJob) {
      const job = await request('GET', `/engine/beauty/api/flow/jobs/${encodeURIComponent(assertJob)}`, null, 10000);
      const rows = assertVideoOk(job);
      console.log(`ASSERT_EXISTING_VIDEO_OK job=${assertJob} image=${rows[0].imageMediaId} video=${rows[0].videoMediaIds.join(',')}`);
      console.log('FLOW_FUNCTION_CHAIN_TEST_OK');
      return 0;
    }
    console.log(`PERSONA ${persona}`);
    await quickImageAttach(persona);
    if (has('--chain-video') || has('--all')) await linkedImageToVideo(persona);
    console.log('FLOW_FUNCTION_CHAIN_TEST_OK');
    return 0;
  } finally {
    if (server && !keepServer) server.kill();
  }
}

main().then(code => process.exit(code)).catch(error => {
  console.error('FLOW_FUNCTION_CHAIN_TEST_FAILED');
  console.error(error && error.stack || error);
  process.exit(1);
});
