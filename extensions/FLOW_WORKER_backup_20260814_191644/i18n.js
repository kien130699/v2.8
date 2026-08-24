(() => {
  'use strict';
  const D={
    'Đồng bộ với V2.8 Server 3000 · 1 submit dispatcher · IMAGE / VIDEO queue':['Đồng bộ với V2.8 Server 3000 · 1 bộ điều phối · hàng đợi ẢNH / VIDEO','Synced with V2.8 Server 3000 · 1 submit dispatcher · IMAGE / VIDEO queues'],
    'Ngôn ngữ / Language':['Ngôn ngữ','Language'], 'Giới hạn luồng':['Giới hạn luồng','Concurrency limits'], 'Ảnh 9 · Video 4':['Ảnh 9 · Video 4','Image 9 · Video 4'],
    'Chạy':['Chạy','Run'], 'Cài đặt':['Cài đặt','Settings'], 'Hướng dẫn':['Hướng dẫn','Guide'],
    'Mỗi dòng:':['Mỗi dòng:','Each line:'],
    'Tự kiểm tra Project + chuyển về All Media trước khi chạy':['Tự kiểm tra dự án + chuyển về Tất cả nội dung trước khi chạy','Auto-check Project + return to All Media before running'],
    'CHẠY 1 TAB / N JOB':['CHẠY 1 TAB / N TÁC VỤ','RUN 1 TAB / N JOBS'], 'Chưa chạy':['Chưa chạy','Not started'],
    '1 tab · submit queue FIFO.':['1 tab · hàng đợi gửi lệnh FIFO.','1 tab · FIFO submit queue.'], 'Sẵn sàng.':['Sẵn sàng.','Ready.'],
    'IMAGE 0/9 · VIDEO 0/4 · SUBMIT I:0 V:0 · DONE 0/0 · ERROR 0':['ẢNH 0/9 · VIDEO 0/4 · GỬI A:0 V:0 · XONG 0/0 · LỖI 0','IMAGE 0/9 · VIDEO 0/4 · SUBMIT I:0 V:0 · DONE 0/0 · ERROR 0'],
    'Popup có thể đóng/mất focus; state vẫn lưu trong background/storage.':['Có thể đóng popup hoặc mất tiêu điểm; trạng thái vẫn được lưu nền.','Popup may close or lose focus; state remains saved in background/storage.'],
    'Server tổng':['Server tổng','Server'], 'Kết nối server':['Kết nối server','Server connection'], 'Tự nhận job từ server':['Tự nhận tác vụ từ server','Automatically receive server jobs'],
    'Trạng thái':['Trạng thái','Status'], '● Đang kiểm tra...':['● Đang kiểm tra...','● Checking...'], 'WebSocket URL':['Địa chỉ WebSocket','WebSocket URL'],
    'Model & đồng thời':['Mô hình & số luồng','Models & concurrency'], 'Image model':['Mô hình ảnh','Image model'], 'Video model':['Mô hình video','Video model'],
    'None — bỏ ảnh':['Không dùng — bỏ ảnh','None — skip image'], 'None — bỏ video':['Không dùng — bỏ video','None — skip video'],
    'Ảnh đồng thời':['Luồng ảnh','Image concurrency'], 'Video đồng thời':['Luồng video','Video concurrency'],
    'Submit Queue & tải video':['Hàng đợi gửi lệnh & tải video','Submit Queue & video download'], 'Chính sách Submit Queue':['Chính sách hàng đợi gửi lệnh','Submit Queue policy'],
    'FIFO nhóm + ưu tiên video nhẹ':['FIFO theo nhóm + ưu tiên video nhẹ','Grouped FIFO + light video priority'], 'FIFO tổng — đến trước chạy trước':['FIFO tổng — đến trước chạy trước','Global FIFO — first in, first out'],
    'Auto download video':['Tự tải video','Auto-download video'], 'Tự tải khi SUCCESSFUL':['Tự tải khi THÀNH CÔNG','Download automatically on SUCCESSFUL'],
    'Max Create / phút':['Tối đa lệnh Tạo / phút','Max Create / minute'], 'Khoảng cách Create (ms)':['Khoảng cách giữa lệnh Tạo (ms)','Create gap (ms)'],
    'Output':['Kết quả','Output'], 'Tỉ lệ':['Tỉ lệ khung hình','Aspect ratio'], 'Số ảnh / Create':['Số ảnh / lần Tạo','Images / Create'],
    'Video duration':['Thời lượng video','Video duration'], 'Video base / Create':['Video gốc / lần Tạo','Base videos / Create'],
    'Server Extend mode giữ base ở x1.':['Chế độ kéo dài của server giữ video gốc ở x1.','Server Extend mode keeps base output at x1.'],
    'Timeout':['Thời gian chờ','Timeout'], 'Timeout ảnh (giây)':['Thời gian chờ ảnh (giây)','Image timeout (seconds)'], 'Timeout video (giây)':['Thời gian chờ video (giây)','Video timeout (seconds)'],
    'Thủ công:':['Thủ công:','Manual:'], 'Server:':['Server:','Server:'], 'Kiến trúc:':['Kiến trúc:','Architecture:'], 'Submit mặc định:':['Gửi lệnh mặc định:','Default submit:'],
    'Ingredient:':['Ảnh tham chiếu:','Ingredient:'], 'Project/View:':['Dự án/Giao diện:','Project/View:'],
    '1 tab · 1 nút Create · 1 submit dispatcher. Ảnh giữ đầy tối đa 9 slot, video tối đa 4 slot. IMAGE và VIDEO chỉ xếp hàng; dispatcher xử lý từng submit nên không đụng Settings/Prompt/Asset Picker cùng lúc.':['1 tab · 1 nút Tạo · 1 bộ điều phối gửi lệnh. Ảnh dùng tối đa 9 luồng, video tối đa 4 luồng. ẢNH và VIDEO chỉ xếp hàng; bộ điều phối xử lý từng lệnh nên không thao tác Cài đặt/Mô tả/Bộ chọn tệp cùng lúc.','1 tab · 1 Create button · 1 submit dispatcher. Images fill up to 9 slots, videos up to 4 slots. IMAGE and VIDEO only queue; the dispatcher handles one submit at a time so Settings/Prompt/Asset Picker do not overlap.'],
    'FIFO từng nhóm + ưu tiên video nhẹ. Trong mỗi nhóm vẫn đúng thứ tự; khi video có slot và đã có ảnh sẵn sàng thì video được lấy trước.':['FIFO theo từng nhóm + ưu tiên video nhẹ. Trong mỗi nhóm vẫn giữ đúng thứ tự; khi video còn luồng và ảnh đã sẵn sàng thì video được lấy trước.','Grouped FIFO with light video priority. Order is preserved inside each group; when a video slot is free and an image is ready, video is picked first.'],
    'nếu Flow dịch prompt ảnh sang tiếng Anh, extension dùng prompt/title mà Flow trả về để lọc Asset Picker, sau đó vẫn bắt buộc chọn đúng':['nếu Flow dịch mô tả ảnh sang tiếng Anh, extension dùng mô tả/tiêu đề Flow trả về để lọc bộ chọn tệp, sau đó vẫn bắt buộc chọn đúng','if Flow translates the image prompt to English, the extension uses the returned prompt/title to filter Asset Picker, then still requires the exact'],
    '. Chuỗi Search được giữ':['. Chuỗi tìm kiếm được giữ','. The Search string is kept'], 'nguyên văn':['nguyên văn','verbatim'], '; không tự thêm hoặc bỏ dấu chấm.':['; không tự thêm hoặc bỏ dấu chấm.','; punctuation is not added or removed automatically.'],
    'khi bấm Chạy, extension kiểm tra URL. Nếu chưa ở Flow thì mở Flow; nếu chưa ở Project thì tự bấm tạo Project mới; sau đó tự chuyển về':['khi bấm Chạy, extension kiểm tra URL. Nếu chưa ở Flow thì mở Flow; nếu chưa ở dự án thì tự tạo dự án mới; sau đó tự chuyển về','when Run is clicked, the extension checks the URL. If not in Flow, it opens Flow; if not in a Project, it creates a new Project; then returns to'],
    'All Media':['Tất cả nội dung','All Media'], 'trước khi thao tác.':['trước khi thao tác.','before automation starts.'],
    'Đóng DevTools trước khi chạy vì extension cần':['Đóng công cụ nhà phát triển trước khi chạy vì extension cần','Close DevTools before running because the extension needs'],
    'Nếu Flow yêu cầu xác minh thủ công, xử lý xác minh rồi chạy lại.':['Nếu Flow yêu cầu xác minh thủ công, hãy xử lý rồi chạy lại.','If Flow asks for manual verification, complete it and run again.'],
    'Max Create/phút và khoảng cách Create phải đặt phù hợp giới hạn tài khoản.':['Số lệnh Tạo tối đa/phút và khoảng cách giữa các lệnh phải phù hợp giới hạn tài khoản.','Set Max Create/minute and Create gap according to account limits.'],
    'Server tổng mặc định:':['Server mặc định:','Default server:'], '. Nếu server đang chạy, extension tự kết nối và nhận job.':['. Nếu server đang chạy, extension tự kết nối và nhận tác vụ.','. If the server is running, the extension connects automatically and receives jobs.'],
    '● Connected':['● Đã kết nối','● Connected'], '○ Disconnected':['○ Mất kết nối','○ Disconnected'],
    'ĐANG CHẠY 1 TAB...':['ĐANG CHẠY 1 TAB...','RUNNING 1 TAB...'], 'Đang gửi queue xuống background...':['Đang gửi hàng đợi xuống tiến trình nền...','Sending queue to background...'],
    'Đang khởi động':['Đang khởi động','Starting'], 'Image model và Video model không thể cùng là None.':['Mô hình ảnh và mô hình video không thể cùng là Không dùng.','Image model and Video model cannot both be None.'],
    'Không xác định được tab hiện tại.':['Không xác định được tab hiện tại.','Unable to determine the current tab.'], 'Automation thất bại.':['Tự động hóa thất bại.','Automation failed.']
  };
  let lang='vi'; const sourceByNode=new WeakMap(); const idx=()=>lang==='vi'?0:1;
  function exact(t){const r=D[t];return r?r[idx()]:null}
  function dyn(t){
    let o=String(t??'');
    const vi=[
      ['Connected','Đã kết nối'],['Disconnected','Mất kết nối'],['Not started','Chưa chạy'],['Ready.','Sẵn sàng.'],['RUNNING','ĐANG CHẠY'],
      ['IMAGE ','ẢNH '],['SUBMIT ','GỬI '],['DONE ','XONG '],['ERROR ','LỖI '],['job','tác vụ'],['queue','hàng đợi'],['background','tiến trình nền'],
      ['Settings','Cài đặt'],['Prompt','Mô tả'],['Asset Picker','Bộ chọn tệp'],['SUCCESSFUL','THÀNH CÔNG'],['Create','Tạo'],['Project','dự án'],['All Media','Tất cả nội dung']
    ];
    const en=[['Đã kết nối','Connected'],['Mất kết nối','Disconnected'],['Chưa chạy','Not started'],['Sẵn sàng.','Ready.'],['tác vụ','job'],['hàng đợi','queue'],['tiến trình nền','background']];
    for(const [a,b] of (lang==='vi'?vi:en))o=o.split(a).join(b);return o;
  }
  function skip(n){const p=n.parentElement;return !p||p.closest('script,style,textarea,code')}
  function trNode(n){if(skip(n))return;let src=sourceByNode.get(n);if(src==null){src=n.nodeValue;sourceByNode.set(n,src)}const z=src.trim();if(!z)return;const out=exact(z)??dyn(z),a=src.match(/^\s*/)?.[0]||'',b=src.match(/\s*$/)?.[0]||'';n.nodeValue=a+out+b}
  function walk(root=document.body){if(!root)return;if(root.nodeType===Node.TEXT_NODE){trNode(root);return}const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;while((n=w.nextNode()))trNode(n)}
  async function setLang(v,persist=true){lang=v==='en'?'en':'vi';document.documentElement.lang=lang;const s=document.getElementById('uiLanguage');if(s)s.value=lang;if(persist)await chrome.storage.local.set({flowUiLanguage:lang});walk(document.body)}
  new MutationObserver(ms=>ms.forEach(m=>m.addedNodes.forEach(walk))).observe(document.documentElement,{subtree:true,childList:true});
  document.addEventListener('DOMContentLoaded',async()=>{const x=await chrome.storage.local.get('flowUiLanguage');await setLang(x.flowUiLanguage||'vi',false);document.getElementById('uiLanguage')?.addEventListener('change',e=>setLang(e.target.value,true))});
})();
