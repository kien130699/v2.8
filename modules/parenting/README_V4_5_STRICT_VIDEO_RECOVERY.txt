PARENTING CONTENT FACTORY V4.5 — RENDER PREFLIGHT + MEDIA SELF-HEAL
===================================================================

MỤC TIÊU
- V4.3 đã cứu được exact mediaId và server download 4/4 video.
- V4.5 sửa tầng SAU download: không cho file tồn tại nhưng MP4 hỏng lọt vào FFmpeg.
- Render lỗi không được treo campaign hoặc generate lại Flow vô tội vạ.

FIX 1 — STRICT MP4 VALIDATION
- Sau khi server download xong, bắt buộc ffprobe thấy video stream + width/height + duration hợp lệ.
- File đủ byte nhưng container hỏng/moov lỗi vẫn bị reject.
- .part hỏng bị xóa và tải lại từ exact mediaId.
- Existing local MP4 từ V4.3 cũng được kiểm tra lại khi resume; file hỏng không còn được tính VIDEO READY.

FIX 2 — SELF-HEAL ĐÚNG THỨ TỰ
LOCAL VIDEO HỎNG / THIẾU
 -> dùng mediaId đã lưu để DOWNLOAD LẠI
 -> nếu mediaId không verify/resolve được sau budget
 -> INVALID mediaId
 -> giữ image checkpoint
 -> CREATE LẠI CHỈ SCENE đó
 -> mediaId mới -> download -> render

Không regenerate Flow chỉ vì FFmpeg lỗi thông thường.

FIX 3 — FFMPEG LOG THẬT
- Thêm -hide_banner -loglevel error.
- Không còn log toàn bộ gcc/configuration che mất nguyên nhân.
- Log dài giữ phần đầu + TAIL, nơi FFmpeg thường ghi lỗi thật.

FIX 4 — CLIP KHÔNG CÓ AUDIO
- Normalize tự thêm silent stereo track nếu clip Flow không có audio stream.
- Tránh concat/music filter chết vì thiếu [0:a].

FIX 5 — KHÔNG TREO SAU HẾT RENDER RETRY
- Render lỗi không liên quan media được retry theo AUTO_FB_RENDER_MAX_RETRIES.
- Hết budget -> render_permanent -> campaign_tick ngay -> skip/release rolling slot.
- Không chờ watchdog/restart mới chạy video tiếp theo.

UPDATE TRÊN V4.3 HIỆN TẠI
1. Dừng server.
2. Giải nén Parenting_Content_Factory_V4_5_PATCH.zip đè lên folder hiện tại.
3. GIỮ NGUYÊN data/factory.sqlite3.
4. BẮT BUỘC reload Flow Extension v14.6.4; server V4.5 từ chối worker < 14.6.4.
5. Chạy run.bat.

LOG MONG ĐỢI VỚI BATCH ĐANG TREO
- Nếu 4 MP4 cũ hợp lệ: render tiếp thẳng.
- Nếu có MP4 hỏng:
  RENDER_MEDIA_RECOVERY / render_media_recovery
  DOWNLOAD RECOVERY
  VIDEO READY
  PARENTING RENDER START
- Nếu mediaId cũ chết:
  VIDEO_MEDIA_REGENERATE_REQUIRED
  FLOW_CHECKPOINT_REQUEUE / RUN_FLOW_JOB scene thiếu
  VIDEO_MEDIA_IDS_READY
  VIDEO READY
- Nếu FFmpeg vẫn lỗi: log phải hiện FFMPEG_EXIT_xxx + lỗi thật ở cuối, không còn chỉ hiện banner.


V4.5 critical fixes
-------------------
- Extension 14.6.4 no longer treats arbitrary media[].name / workflow.primaryMediaId as VIDEO.
- Rejects image/reference media IDs before VIDEO_MEDIA_IDS_READY.
- Valid local MP4 wins over late signed-url errors; never invalidate/download/regenerate it again.
- permanent_failed checkpoint cannot be revived by CHECKPOINT_RECOVERY.
- Known 14.6.3 image/jpeg wrong-video checkpoints are repaired on startup without deleting valid MP4 assets.
- Windows ASS burn-in uses relative dialogue.ass in cwd, fixing drive-colon/original_size parse errors.
