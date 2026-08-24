(() => {
  'use strict';
  const STORAGE_KEY='fcc_ui_language';
  let lang=(localStorage.getItem(STORAGE_KEY)||'vi').toLowerCase()==='en'?'en':'vi';

  // [Vietnamese, English]. Product/model/internal identifiers stay unchanged on purpose.
  const SETTINGS_EXTRA={
    '1. Video & dựng':['1. Video & dựng','1. Video & rendering'],
    '2. Lịch đăng & Facebook':['2. Lịch đăng & Facebook','2. Schedule & Facebook'],
    '3. Nội dung & Persona':['3. Nội dung & Persona','3. Content & Persona'],
    '4. Test / Debug':['4. Test / Debug','4. Test / Debug'],
    '5. Hệ thống · Jobs · Output':['5. Hệ thống · Jobs · Output','5. System · Jobs · Output'],
    '6. Nhật ký':['6. Nhật ký','6. Logs'],
    'Cài đặt':['Cài đặt','Settings']
  };
  const D={
    'ẢNH + VIDEO':['ẢNH + VIDEO','IMAGE + VIDEO'],
    'AUTO RANDOM · ẢNH / ẢNH+VIDEO / ẢNH→VIDEO':['AUTO RANDOM · ẢNH / ẢNH+VIDEO / ẢNH→VIDEO','AUTO RANDOM · IMAGE / IMAGE+VIDEO / IMAGE→VIDEO'],
    'XÓA HỒ SƠ':['XÓA HỒ SƠ','DELETE PROFILE'],
    'NHÂN HỒ SƠ · CLEAR ẢNH':['NHÂN HỒ SƠ · CLEAR ẢNH','CLONE PROFILE · CLEAR IMAGES'],

    'Nhạc TikTok / CapCut':['Nhạc TikTok / CapCut','TikTok / CapCut Music'],
    'TẢI MP3':['TẢI MP3','IMPORT MP3'],

    'Chaos Mix — SIÊU GIẬT':['Chaos Mix — SIÊU GIẬT','Chaos Mix — SUPER SHAKE'],
    'Impact Shake — GIẬT MẠNH':['Impact Shake — GIẬT MẠNH','Impact Shake — HARD'],
    'Whip Shake — QUĂNG CẢNH':['Whip Shake — QUĂNG CẢNH','Whip Shake — HARD WHIP'],
    'Flash Smash — ĐẬP FLASH':['Flash Smash — ĐẬP FLASH','Flash Smash — IMPACT FLASH'],
    'Ảnh theo nhịp (15s ≈ 10 ảnh)':['Ảnh theo nhịp (15s ≈ 10 ảnh)','Beat images (15s ≈ 10 images)'],

    'Pages':['Pages','Pages'], 'Auto':['Auto','Auto'],
    'Thiết lập Page':['Thiết lập Page','Page setup'], 'Danh sách Page':['Danh sách Page','Page list'],
    'Token Facebook':['Token Facebook','Facebook token'], 'IMPORT TOKEN PAGE':['IMPORT TOKEN PAGE','IMPORT PAGE TOKEN'],
    'Chọn Page':['Chọn Page','Select Page'], 'Import ảnh mặt':['Import ảnh mặt','Import face image'],
    'ẢNH THEO NHỊP':['ẢNH THEO NHỊP','BEAT IMAGES'], 'ẢNH → VIDEO AI':['ẢNH → VIDEO AI','IMAGE → AI VIDEO'],
    'LƯU PAGE':['LƯU PAGE','SAVE PAGE'], 'Cài đặt nâng cao':['Cài đặt nâng cao','Advanced settings'],
    'MỞ TEST / DEBUG':['MỞ TEST / DEBUG','OPEN TEST / DEBUG'], 'TRẠNG THÁI HỆ THỐNG':['TRẠNG THÁI HỆ THỐNG','SYSTEM STATUS'],
    'Tự động sản xuất & đăng':['Tự động sản xuất & đăng','Automated production & publishing'],
    'Chọn Trang, cấu hình video và lịch một lần. Sau khi bật, hệ thống tự giữ video dự phòng, tạo bù khi thiếu và chỉ đăng khi tới giờ.':['Chọn Trang, cấu hình video và lịch một lần. Sau khi bật, hệ thống tự giữ video dự phòng, tạo bù khi thiếu và chỉ đăng khi tới giờ.','Choose a Page, video settings and schedule once. After enabling, the system maintains the video buffer, refills when needed and publishes only when due.'],
    'Hồ sơ Trang':['Hồ sơ Trang','Page profile'], 'Chế độ video':['Chế độ video','Video mode'], 'Video dự phòng':['Video dự phòng','Video buffer'],
    'Ảnh theo nhịp':['Ảnh theo nhịp','Beat images'], 'Thời lượng video ảnh':['Thời lượng video ảnh','Beat video duration'], 'I2V · số đoạn':['I2V · số đoạn','I2V clips'], 'I2V · mỗi đoạn':['I2V · mỗi đoạn','I2V clip duration'],
    'Lịch đăng':['Lịch đăng','Publishing schedule'], 'Kiểu lịch':['Kiểu lịch','Schedule type'], 'Facebook theo lịch':['Facebook theo lịch','Scheduled Facebook mode'], 'Múi giờ':['Múi giờ','Time zone'],
    'MỐC GIỜ HẰNG NGÀY':['MỐC GIỜ HẰNG NGÀY','DAILY TIME SLOTS'], 'KHOẢNG CÁCH · mỗi X phút':['KHOẢNG CÁCH · mỗi X phút','INTERVAL · every X minutes'],
    'Giờ đăng/ngày':['Giờ đăng/ngày','Daily publish times'], 'Ngẫu nhiên ± phút':['Ngẫu nhiên ± phút','Random ± minutes'], 'Mở lại server · bù trong 0→phút':['Mở lại server · bù trong 0→phút','Server restart · catch up within 0→minutes'],
    'Đăng mỗi (phút)':['Đăng mỗi (phút)','Publish every (minutes)'], 'Bài đầu sau (phút)':['Bài đầu sau (phút)','First post after (minutes)'],
    'BẬT TỰ ĐỘNG':['BẬT TỰ ĐỘNG','START AUTOMATION'], 'DỪNG':['DỪNG','STOP'], 'TEST TẢI VIDEO GẦN NHẤT':['TEST TẢI VIDEO GẦN NHẤT','TEST LATEST VIDEO DOWNLOAD'], 'BỎ QUA JOB LỖI':['BỎ QUA JOB LỖI','DISCARD FAILED JOBS'],
    'Cài đặt nâng cao':['Cài đặt nâng cao','Advanced settings'], 'Kiểu chuyển cảnh':['Kiểu chuyển cảnh','Transition preset'], 'Luồng ảnh':['Luồng ảnh','Image concurrency'], 'Luồng video':['Luồng video','Video concurrency'],
    'Mặc định Ảnh 9 luồng / Video 4 luồng. Chỉ đổi khi Flow hoặc máy bị quá tải.':['Mặc định Ảnh 9 luồng / Video 4 luồng. Chỉ đổi khi Flow hoặc máy bị quá tải.','Default: 9 image workers / 4 video workers. Change only if Flow or the machine is overloaded.'],
    'Công cụ thủ công / kiểm tra':['Công cụ thủ công / kiểm tra','Manual / test tools'], 'Thao tác':['Thao tác','Action'], 'THỰC HIỆN':['THỰC HIỆN','RUN'],
    'Tạo đủ video dự phòng':['Tạo đủ video dự phòng','Fill video buffer'], 'Đăng 1 video READY ngay':['Đăng 1 video READY ngay','Publish one READY video now'], 'Kiểm thử 1 video':['Kiểm thử 1 video','Test one video'], 'Xem trước tiêu đề / nội dung AI':['Xem trước tiêu đề / nội dung AI','Preview AI title / caption'], 'Tạo batch thủ công':['Tạo batch thủ công','Create manual batch'],
    'Bạn vừa bấm nút này. Vui lòng chờ thao tác trước xử lý xong.':['Bạn vừa bấm nút này. Vui lòng chờ thao tác trước xử lý xong.','You just clicked this button. Please wait for the current action to finish.'],
    'Thao tác này vừa thực hiện thành công. Không gửi lặp lần hai.':['Thao tác này vừa thực hiện thành công. Không gửi lặp lần hai.','This action just completed successfully. The duplicate request was not sent.'],
    'Thao tác này đang xử lý. Không gửi request trùng.':['Thao tác này đang xử lý. Không gửi request trùng.','This action is already in progress. A duplicate request was not sent.'],
    'Thao tác thành công.':['Thao tác thành công.','Action completed successfully.'],
    'Đã lưu hồ sơ Trang.':['Đã lưu hồ sơ Trang.','Page profile saved.'],
    'Đã tạo lại FRONT chuẩn thành công.':['Đã tạo lại FRONT chuẩn thành công.','FRONT master rebuilt successfully.'],
    'Đã kiểm tra/tạo các góc còn thiếu.':['Đã kiểm tra/tạo các góc còn thiếu.','Missing angles checked/generated.'],
    'Đã gửi yêu cầu tạo góc.':['Đã gửi yêu cầu tạo góc.','Angle generation request queued.'],
    'Đã bật/cập nhật lịch đăng.':['Đã bật/cập nhật lịch đăng.','Publishing schedule enabled/updated.'],
    'Đã dừng lịch đăng.':['Đã dừng lịch đăng.','Publishing schedule stopped.'],
    'Đã kiểm tra và tạo bù hàng đợi.':['Đã kiểm tra và tạo bù hàng đợi.','Queue checked and refilled.'],
    'Đã xử lý yêu cầu đăng ngay.':['Đã xử lý yêu cầu đăng ngay.','Publish-now request processed.'],
    'Đã tạo batch thành công.':['Đã tạo batch thành công.','Batch created successfully.'],
    'Đã xóa thành công.':['Đã xóa thành công.','Deleted successfully.'],
    'Hãy LƯU HỒ SƠ trước.':['Hãy LƯU HỒ SƠ trước.','Please SAVE PROFILE first.'],
    'Chọn Page Profile trước.':['Chọn Page Profile trước.','Select a Page Profile first.'],
    'Lịch của Page này đã bật với đúng cấu hình hiện tại — không chạy lại, không tạo video trùng.':['Lịch của Page này đã bật với đúng cấu hình hiện tại — không chạy lại, không tạo video trùng.','This Page schedule is already enabled with the same settings — nothing restarted and no duplicate video was created.'],
    'Không có ảnh FRONT gốc để tạo lại. Hãy tải lại FRONT rồi LƯU HỒ SƠ.':['Không có ảnh FRONT gốc để tạo lại. Hãy tải lại FRONT rồi LƯU HỒ SƠ.','Original FRONT image is missing. Upload FRONT again and SAVE PROFILE.'],
    'Flow Content Factory':['Flow Content Factory','Flow Content Factory'],
    'V2.14.29 · Image 9 / Video 4 · Persona Verify · Vietnam Lifestyle':['V2.14.29 · Ảnh 9 / Video 4 · Xác minh Persona · Lifestyle Việt Nam','V2.14.29 · Image 9 / Video 4 · Persona Verify · Vietnam Lifestyle'],
    'Dashboard':['Tổng quan','Dashboard'],
    'Workbench · Test & Preview':['Kiểm thử · Xem trước','Workbench · Test & Preview'],
    'Auto Factory · 100%':['Sản xuất tự động · 100%','Auto Factory · 100%'],
    'Logs':['Nhật ký','Logs'],
    'Ngôn ngữ / Language':['Ngôn ngữ','Language'],
    'PING AGENT':['KIỂM TRA TÁC NHÂN','PING AGENT'],
    'Extension giữ ws://127.0.0.1:8786/ws · Web UI tự chọn cổng':['Extension giữ ws://127.0.0.1:8786/ws · Web tự chọn cổng','Extension stays on ws://127.0.0.1:8786/ws · Web UI auto-selects port'],
    'Flow agents':['Tác nhân Flow','Flow agents'],
    'Flow agent':['Tác nhân Flow','Flow agent'],
    'Job active':['Tác vụ đang chạy','Active jobs'],
    'Page profiles':['Hồ sơ Trang','Page profiles'],
    'Final videos':['Video hoàn tất','Final videos'],
    'Flow Agents':['Tác nhân Flow','Flow Agents'],
    'Agent':['Tác nhân','Agent'], 'Version':['Phiên bản','Version'], 'Status':['Trạng thái','Status'], 'Job':['Tác vụ','Job'],
    'Time':['Thời gian','Time'], 'Type':['Loại','Type'], 'Nội dung':['Nội dung','Content'],
    'SHORT = dễ đọc theo tiến trình · FULL = payload đầy đủ từ server/extension.':['NGẮN = tiến trình dễ đọc · ĐẦY ĐỦ = dữ liệu gốc từ server/extension.','SHORT = readable progress · FULL = complete server/extension payload.'],
    'LOG NGẮN GỌN':['NHẬT KÝ NGẮN','SHORT LOG'], 'LOG FULL':['NHẬT KÝ ĐẦY ĐỦ','FULL LOG'],
    'REFRESH':['LÀM MỚI','REFRESH'], 'CLEAR':['XÓA','CLEAR'], 'Lọc nhanh':['Lọc nhanh','Quick filter'], 'Số dòng':['Số dòng','Rows'],

    'Test Full Video':['Kiểm thử video hoàn chỉnh','Full Video Test'],
    'Dùng để test tạo video trước: persona + optional outfit + nhạc → Flow ảnh → render video → preview.':['Dùng để kiểm thử: persona + trang phục tùy chọn + nhạc → Flow tạo ảnh → dựng video → xem trước.','Test before production: persona + optional outfit + music → Flow images → render video → preview.'],
    'Prompt':['Mô tả AI','Prompt'], 'Số ảnh':['Số ảnh','Image count'], 'Duration':['Thời lượng','Duration'], 'Motion':['Chuyển động','Motion'],
    'CapCut Beat':['CapCut theo nhịp','CapCut Beat'], 'Flash Cut':['Cắt chớp','Flash Cut'], 'Mix':['Kết hợp','Mix'], 'Smooth':['Mượt','Smooth'],
    'Concurrency':['Luồng ảnh','Image concurrency'], 'Person reference':['Ảnh người tham chiếu','Person reference'], 'Outfit reference':['Ảnh trang phục tham chiếu','Outfit reference'],
    'Music':['Nhạc','Music'], 'Kết quả':['Kết quả','Result'], 'TEST FULL VIDEO':['KIỂM THỬ VIDEO','TEST FULL VIDEO'], 'Preview test':['Xem trước kết quả','Test preview'],

    '9Router · Test Models':['9Router · Kiểm tra mô hình','9Router · Test Models'],
    'GET /v1/models → tự chặn toàn bộ gh/GitHub. model_not_supported = permanent block và biến khỏi danh sách. Lỗi tạm có thể soft-clear/retest. AUTO chỉ dùng model TEST OK.':['GET /v1/models → tự chặn toàn bộ gh/GitHub. model_not_supported = chặn vĩnh viễn và loại khỏi danh sách. Lỗi tạm có thể xóa mềm/kiểm tra lại. TỰ ĐỘNG chỉ dùng mô hình đã KIỂM TRA OK.','GET /v1/models → blocks all gh/GitHub models. model_not_supported = permanent block and removed from the list. Temporary errors can be soft-cleared/retested. AUTO only uses TEST OK models.'],
    'REFRESH MODELS':['LÀM MỚI MÔ HÌNH','REFRESH MODELS'], 'TEST TẤT CẢ GPT/GEMINI':['KIỂM TRA TẤT CẢ GPT/GEMINI','TEST ALL GPT/GEMINI'],
    'CLEAR MODEL LỖI':['XÓA MÔ HÌNH LỖI','CLEAR FAILED MODELS'], 'KHÔI PHỤC MODEL ĐÃ CLEAR':['KHÔI PHỤC MÔ HÌNH ĐÃ XÓA','RESTORE CLEARED MODELS'],
    'Family':['Nhóm','Family'], 'Model':['Mô hình','Model'], 'Latency':['Độ trễ','Latency'], 'Lỗi':['Lỗi','Error'],
    'TEST / RETEST':['KIỂM TRA / KIỂM TRA LẠI','TEST / RETEST'],

    'Facebook Publish Test':['Kiểm thử đăng Facebook','Facebook Publish Test'],
    'Test token, preflight và đăng thử hoặc đăng thật lên 1 Page.':['Kiểm tra token, kiểm tra trước và đăng thử/đăng thật lên một Trang.','Test token, preflight, dry-run or publish to one Page.'],
    'Page':['Trang','Page'], 'Video local path':['Đường dẫn video trên máy','Local video path'], 'Title':['Tiêu đề','Title'], 'Mode':['Chế độ','Mode'],
    'DRY RUN':['CHẠY THỬ','DRY RUN'], 'PUBLISH THẬT':['ĐĂNG THẬT','PUBLISH LIVE'], 'Caption':['Nội dung bài đăng','Caption'],
    'PREFLIGHT':['KIỂM TRA TRƯỚC','PREFLIGHT'], 'SUBMIT REEL':['GỬI REEL','SUBMIT REEL'], 'Publish Jobs':['Tác vụ đăng bài','Publish Jobs'],
    'Jobs':['Tác vụ','Jobs'], 'Assets / Output':['Tệp / Kết quả','Assets / Output'],
    'Facebook Pages':['Các Trang Facebook','Facebook Pages'], 'User Access Token':['Token người dùng','User Access Token'], 'SYNC PAGES':['ĐỒNG BỘ TRANG','SYNC PAGES'],
    'CHO PHÉP SYNC LẠI PAGE ĐÃ XÓA':['CHO PHÉP ĐỒNG BỘ LẠI TRANG ĐÃ XÓA','ALLOW DELETED PAGES TO SYNC AGAIN'],
    'Page ID':['ID Trang','Page ID'], 'Tên':['Tên','Name'], 'Page Access Token':['Token Trang','Page Access Token'], 'SAVE PAGE':['LƯU TRANG','SAVE PAGE'],

    'Page Profile · 1 mặt / 1 Page':['Hồ sơ Trang · 1 nhân vật / 1 Trang','Page Profile · 1 persona / 1 Page'], 'Profile ID':['ID hồ sơ','Profile ID'],
    'Tên Page/Profile':['Tên Trang/Hồ sơ','Page/Profile name'], 'Theme':['Chủ đề','Theme'],
    'Persona gốc FRONT (server sẽ tự crop mặt + tạo master 2048)':['Ảnh FRONT gốc (server tự cắt khuôn mặt + tạo bản chuẩn 2048)','Original FRONT persona (server auto-crops face + creates 2048 master)'],
    'Body preset':['Dáng người','Body preset'], 'Slim Fit':['Mảnh mai săn chắc','Slim Fit'], 'Curvy Fit':['Đầy đặn săn chắc','Curvy Fit'],
    'Glam Curvy':['Đầy đặn quyến rũ','Glam Curvy'], 'Soft Feminine':['Mềm mại nữ tính','Soft Feminine'], 'Sporty Curvy':['Thể thao đầy đặn','Sporty Curvy'],
    'Sexy/Glam 0–100':['Độ quyến rũ 0–100','Sexy/Glam 0–100'],
    'Multi-angle Persona Pack · Angle Manager':['Bộ Persona nhiều góc · Quản lý góc','Multi-angle Persona Pack · Angle Manager'],
    'SAVE PROFILE chỉ lưu FRONT, không tự spam job nữa. Bấm GEN CÁC GÓC CÒN THIẾU một lần; sau đó mỗi card LEFT / RIGHT / BACK có DÙNG/BỎ DÙNG · GEN LẠI · XÓA riêng.':['LƯU HỒ SƠ chỉ lưu FRONT, không tự tạo lặp. Bấm TẠO CÁC GÓC CÒN THIẾU một lần; sau đó quản lý riêng TRÁI / PHẢI / SAU bằng DÙNG/BỎ DÙNG · TẠO LẠI · XÓA.','SAVE PROFILE stores FRONT only and does not auto-spam jobs. Click GENERATE MISSING ANGLES once; then manage LEFT / RIGHT / BACK independently with USE/DISABLE · REGENERATE · DELETE.'],
    'REBUILD FRONT MASTER':['TẠO LẠI FRONT CHUẨN','REBUILD FRONT MASTER'], 'GEN CÁC GÓC CÒN THIẾU':['TẠO CÁC GÓC CÒN THIẾU','GENERATE MISSING ANGLES'],
    'Chưa kiểm tra Persona Pack.':['Chưa kiểm tra bộ Persona.','Persona Pack not checked yet.'],
    'Outfit prompt pool — 1 dòng 1 outfit':['Danh sách trang phục — mỗi dòng 1 bộ','Outfit prompt pool — one outfit per line'],
    'Outfit reference paths (optional)':['Đường dẫn ảnh trang phục tham chiếu (tùy chọn)','Outfit reference paths (optional)'], 'Music paths (optional)':['Đường dẫn nhạc (tùy chọn)','Music paths (optional)'],
    'Background pool':['Danh sách bối cảnh','Background pool'], 'Pose pool':['Danh sách tư thế','Pose pool'], 'Default mode':['Chế độ mặc định','Default mode'],
    'AUTO':['TỰ ĐỘNG','AUTO'], 'IMAGE_BEAT':['ẢNH THEO NHỊP','IMAGE_BEAT'], 'IMAGE_TO_VIDEO':['ẢNH → VIDEO','IMAGE_TO_VIDEO'],
    '% Image→Video khi AUTO':['% Ảnh→Video khi TỰ ĐỘNG','% Image→Video in AUTO'], 'Map Facebook Page':['Gán Trang Facebook','Map Facebook Page'], '— Chưa map —':['— Chưa gán —','— Not mapped —'],
    'Image model':['Mô hình ảnh','Image model'], 'Video model':['Mô hình video','Video model'], 'Enabled':['Kích hoạt','Enabled'], 'ON':['BẬT','ON'], 'OFF':['TẮT','OFF'],
    'AI Writer / Planner (9router)':['AI viết nội dung / Lập kế hoạch (9Router)','AI Writer / Planner (9Router)'], 'Đang kiểm tra 9router...':['Đang kiểm tra 9Router...','Checking 9Router...'],
    'Title hint':['Gợi ý tiêu đề','Title hint'], 'Caption style':['Kiểu nội dung','Caption style'], 'Engaging Short':['Ngắn gọn cuốn hút','Engaging Short'], 'Sexy Soft':['Quyến rũ nhẹ','Sexy Soft'], 'Fashion FB':['Thời trang Facebook','Fashion FB'],
    'AI model (9router)':['Mô hình AI (9Router)','AI model (9Router)'], 'AUTO — GPT/Gemini đầu tiên':['TỰ ĐỘNG — GPT/Gemini đầu tiên','AUTO — first available GPT/Gemini'],
    'AUTO — chỉ ưu tiên model TEST OK':['TỰ ĐỘNG — chỉ ưu tiên mô hình đã KIỂM TRA OK','AUTO — only prioritize TEST OK models'],
    'SAVE PROFILE':['LƯU HỒ SƠ','SAVE PROFILE'], 'NEW':['TẠO MỚI','NEW'], 'RERUN PREPARE':['TẠO LẠI FRONT CHUẨN','REBUILD FRONT'], 'Profiles':['Hồ sơ','Profiles'],

    'Auto Factory':['Sản xuất tự động','Auto Factory'],
    'Sau khi gán persona front + các góc trái/phải/sau cho Page, server sẽ tự tạo multi-angle persona pack rồi dùng cả bộ ref này cho Auto Factory. AI outfit ưu tiên quần ngắn, áo mát mẻ, phong cách quyến rũ, vóc dáng đầy đặn nhưng vẫn fully clothed / non-explicit.':['Sau khi có FRONT và các góc TRÁI/PHẢI/SAU, server dùng toàn bộ ảnh tham chiếu cho sản xuất tự động. AI trang phục ưu tiên quần ngắn, áo mát, phong cách quyến rũ nhưng vẫn mặc kín và không phản cảm.','After FRONT plus LEFT/RIGHT/BACK references are ready, the server uses the full persona pack for Auto Factory. Outfit AI prioritizes short bottoms, cool tops and glamorous styling while staying fully clothed and non-explicit.'],
    'Page Profile':['Hồ sơ Trang','Page Profile'], 'Số final video':['Số video thành phẩm','Final video count'], 'Beat: số ảnh':['Theo nhịp: số ảnh','Beat: image count'],
    'Beat: duration':['Theo nhịp: thời lượng','Beat: duration'], 'Beat preset':['Kiểu chuyển cảnh','Beat preset'], 'Image concurrency':['Luồng ảnh','Image concurrency'],
    'I2V: số clip':['I2V: số đoạn','I2V: clip count'], 'I2V: mỗi clip':['I2V: thời lượng mỗi đoạn','I2V: clip duration'], 'Video concurrency':['Luồng video','Video concurrency'],
    'Đăng ngay sau render':['Đăng ngay sau khi dựng','Publish immediately after render'], 'Manual batch · Facebook':['Batch thủ công · Facebook','Manual batch · Facebook'],
    'DRY RUN — an toàn':['CHẠY THỬ — an toàn','DRY RUN — safe'], 'Output':['Kết quả','Output'],
    '1080×1920 · QC PASS mới auto publish':['1080×1920 · chỉ tự đăng khi QC ĐẠT','1080×1920 · auto-publish only after QC PASS'],

    'Publish Scheduler · tạo trước rồi đăng theo giờ':['Lịch đăng · tạo trước rồi đăng theo giờ','Publish Scheduler · pre-generate then publish on schedule'],
    'Scheduler dùng các thông số generate phía trên. Khi bật, video QC PASS được đưa vào hàng đợi READY, không đăng ngay. Server luôn giữ đủ buffer video sẵn.':['Lịch đăng dùng cấu hình tạo video phía trên. Khi bật, video đạt QC được đưa vào hàng đợi SẴN SÀNG thay vì đăng ngay. Server luôn cố giữ đủ số video dự phòng.','Scheduler uses the generation settings above. When enabled, QC-passed videos enter the READY queue instead of publishing immediately. The server keeps the target video buffer filled.'],
    'Kiểu lịch':['Kiểu lịch','Schedule mode'], 'INTERVAL · cách X phút':['KHOẢNG CÁCH · mỗi X phút','INTERVAL · every X minutes'], 'DAILY SLOTS · giờ cố định/ngày':['MỐC GIỜ HẰNG NGÀY · giờ cố định','DAILY SLOTS · fixed times per day'],
    'Đăng mỗi (phút) · INTERVAL':['Đăng mỗi (phút) · KHOẢNG CÁCH','Publish every (minutes) · INTERVAL'], 'Buffer video sẵn':['Video dự phòng sẵn','Ready video buffer'],
    'Facebook Scheduler':['Facebook theo lịch','Facebook Scheduler'], 'Giờ đăng/ngày · DAILY SLOTS':['Giờ đăng/ngày · MỐC GIỜ','Daily publish times · DAILY SLOTS'],
    'Random ± phút':['Ngẫu nhiên ± phút','Random ± minutes'], 'Restart random 0→phút':['Khi mở lại: ngẫu nhiên 0→phút','Restart catch-up random 0→minutes'],
    'Bài đầu sau · INTERVAL':['Bài đầu sau · KHOẢNG CÁCH','First post delay · INTERVAL'], 'Múi giờ':['Múi giờ','Timezone'],
    'Ví dụ DAILY SLOTS: 08:00,14:00,21:00 + random ±30 phút. Lịch random của ngày được lưu DB nên restart không bốc lại giờ mới. Nếu server mở sau giờ đã lỡ, chỉ catch-up tối đa 1 bài trong 0–30 phút để tránh spam.':['Ví dụ MỐC GIỜ: 08:00, 14:00, 21:00 + ngẫu nhiên ±30 phút. Lịch ngẫu nhiên trong ngày được lưu DB nên mở lại server không đổi giờ. Nếu server mở sau giờ bị lỡ, chỉ đăng bù tối đa 1 bài trong 0–30 phút để tránh dồn bài.','Example DAILY SLOTS: 08:00, 14:00, 21:00 + random ±30 minutes. The randomized daily plan is stored in DB, so restart does not reroll times. If the server starts after missed slots, it catches up at most one post within 0–30 minutes to avoid spam.'],
    'START SCHEDULER':['BẬT LỊCH ĐĂNG','START SCHEDULER'], 'STOP':['DỪNG','STOP'], 'FILL NOW':['TẠO BÙ NGAY','FILL NOW'], 'ĐĂNG 1 BÀI NGAY':['ĐĂNG 1 BÀI NGAY','PUBLISH 1 NOW'], 'REFRESH QUEUE':['LÀM MỚI HÀNG ĐỢI','REFRESH QUEUE'],
    'GENERATE BATCH':['TẠO BATCH','GENERATE BATCH'], 'TEST VIDEO ONLY':['CHỈ KIỂM THỬ VIDEO','TEST VIDEO ONLY'],
    'AI PREVIEW TITLE/CAPTION':['AI XEM TRƯỚC TIÊU ĐỀ/NỘI DUNG','AI PREVIEW TITLE/CAPTION'], 'AUTO TEST 1 PAGE · DRY RUN':['TỰ ĐỘNG KIỂM THỬ 1 TRANG · CHẠY THỬ','AUTO TEST 1 PAGE · DRY RUN'],
    'AUTO TEST + ĐĂNG THẬT':['TỰ ĐỘNG KIỂM THỬ + ĐĂNG THẬT','AUTO TEST + PUBLISH LIVE'], 'Output batch gần nhất':['Kết quả batch gần nhất','Latest batch output'], 'Factory Runs / QC':['Lượt sản xuất / QC','Factory Runs / QC'],

    // Dynamic tables/buttons/cards
    'Original':['Ảnh gốc','Original'], 'Face Crop 1024':['Khuôn mặt 1024','Face Crop 1024'], 'FRONT Master 2048':['FRONT chuẩn 2048','FRONT Master 2048'], 'Bust 2048':['Nửa người 2048','Bust 2048'],
    'LEFT 3/4':['TRÁI 3/4','LEFT 3/4'], 'RIGHT 3/4':['PHẢI 3/4','RIGHT 3/4'], 'BACK HAIR':['PHÍA SAU / TÓC','BACK HAIR'],
    'DÙNG':['DÙNG','USE'], 'BỎ DÙNG':['BỎ DÙNG','DISABLE'], 'GEN LẠI':['TẠO LẠI','REGENERATE'], 'GEN GÓC':['TẠO GÓC','GENERATE ANGLE'], 'XÓA':['XÓA','DELETE'],
    'ĐANG DÙNG':['ĐANG DÙNG','IN USE'], 'ĐÃ BỎ DÙNG':['ĐÃ BỎ DÙNG','DISABLED'], 'missing':['thiếu','missing'],
    'EDIT':['SỬA','EDIT'], 'PREPARE':['TẠO FRONT CHUẨN','PREPARE'], 'DELETE':['XÓA','DELETE'], 'PREVIEW':['XEM TRƯỚC','PREVIEW'], 'RETRY':['CHẠY LẠI','RETRY'], 'ASSETS':['TỆP','ASSETS'],
    'Tasks':['Quyền','Tasks'], 'Test':['Kiểm tra','Test'], 'Quản lý':['Quản lý','Manage'], 'TEST TOKEN':['KIỂM TRA TOKEN','TEST TOKEN'], 'GIỮ DUY NHẤT':['CHỈ GIỮ TRANG NÀY','KEEP ONLY'],
    'Run':['Lượt chạy','Run'], 'Profile':['Hồ sơ','Profile'], 'Progress':['Tiến độ','Progress'], 'Kind':['Loại','Kind'], 'Scenes':['Cảnh','Scenes'], 'Error':['Lỗi','Error'],
    'Queue':['Hàng đợi','Queue'], 'Video':['Video','Video'], 'Scheduled':['Lịch dự kiến','Scheduled'], 'Dry':['Chạy thử','Dry'], 'Video ID':['ID Video','Video ID'],
    'NẠP PRESET VIỆT NAM':['NẠP PRESET VIỆT NAM','LOAD VIETNAM PRESET'], 'TEST TẢI VIDEO GẦN NHẤT':['TEST TẢI VIDEO GẦN NHẤT','TEST LATEST VIDEO DOWNLOAD'],
    'Chưa kiểm tra ảnh FRONT.':['Chưa kiểm tra ảnh FRONT.','FRONT image not verified yet.'], 'Ảnh FRONT gốc · ảnh mặt tham chiếu':['Ảnh FRONT gốc · ảnh mặt tham chiếu','Original FRONT · face reference'],
    'YES':['CÓ','YES'], 'NO':['KHÔNG','NO'], 'READY':['SẴN SÀNG','READY'], 'GENERATING':['ĐANG TẠO','GENERATING'], 'PUBLISHED':['ĐÃ ĐĂNG','PUBLISHED'],
    'ĐƯA SANG FACEBOOK':['DÙNG CHO FACEBOOK','USE ON FACEBOOK'], 'Chưa có Flow extension kết nối.':['Chưa có Flow extension kết nối.','No Flow extension connected.'],
    'Không thấy GPT/Gemini model từ 9router.':['Không thấy mô hình GPT/Gemini từ 9Router.','No GPT/Gemini models found from 9Router.'], 'Chưa có log.':['Chưa có nhật ký.','No logs yet.'],
    'Chưa có Persona. Upload FRONT rồi SAVE PROFILE.':['Chưa có Persona. Tải ảnh FRONT rồi LƯU HỒ SƠ.','No Persona yet. Upload FRONT then SAVE PROFILE.'],
    '— Chưa có profile —':['— Chưa có hồ sơ —','— No profile —'], 'Chưa có profile.':['Chưa có hồ sơ.','No profiles yet.'],
    'Persona prepared · MASTER 2048 READY':['Đã tạo FRONT chuẩn · BẢN CHUẨN 2048 SẴN SÀNG','Persona prepared · MASTER 2048 READY'], 'Queue chưa có video.':['Hàng đợi chưa có video.','Queue has no video yet.'],
    'AUTO TEST 1 PAGE':['TỰ ĐỘNG KIỂM THỬ 1 TRANG','AUTO TEST 1 PAGE'], 'ID':['ID','ID'],
    'SCHEDULER ON':['LỊCH ĐĂNG BẬT','SCHEDULER ON'], 'SCHEDULER OFF':['LỊCH ĐĂNG TẮT','SCHEDULER OFF'], 'Next':['Tiếp theo','Next'], 'Last':['Lần cuối','Last'],
    'FULL PAYLOAD':['DỮ LIỆU ĐẦY ĐỦ','FULL PAYLOAD'], 'NGẮN GỌN':['NGẮN GỌN','SHORT'],
    'STRICT OK ONLY':['CHỈ DÙNG MÔ HÌNH OK','STRICT OK ONLY'], 'CLEARED':['ĐÃ XÓA MỀM','CLEARED'], 'ERROR':['LỖI','ERROR'], 'TESTING':['ĐANG KIỂM TRA','TESTING'], 'UNTESTED':['CHƯA KIỂM TRA','UNTESTED'],
    'PASS':['ĐẠT','PASS'], 'FAIL':['KHÔNG ĐẠT','FAIL'],
    'queued':['đang chờ','queued'], 'dispatching':['đang phân phối','dispatching'], 'running':['đang chạy','running'], 'flow_done':['Flow hoàn tất','Flow done'],
    'rendering':['đang dựng','rendering'], 'qc':['đang QC','QC'], 'done':['hoàn tất','done'], 'qc_passed':['QC đạt','QC passed'], 'published':['đã đăng','published'],
    'dry_run_ok':['chạy thử đạt','dry run OK'], 'failed':['thất bại','failed'], 'qc_failed':['QC không đạt','QC failed'], 'partial_failed':['lỗi một phần','partial failure'],
    'interrupted':['bị gián đoạn','interrupted'], 'ready':['sẵn sàng','ready'], 'generating':['đang tạo','generating'], 'publishing':['đang đăng','publishing'], 'skipped':['đã bỏ qua','skipped'], 'pending':['đang chờ','pending'], 'testing':['đang kiểm tra','testing'], 'untested':['chưa kiểm tra','untested']
  };

  const DEFAULT_FIELDS={
    vtPrompt:{vi:'Ảnh chân thực về một phụ nữ Việt Nam trưởng thành 21+, cuốn hút tự nhiên, giữ đúng danh tính, phong cách lifestyle Việt Nam hiện đại, kết cấu da chân thực.',en:'Photorealistic adult Vietnamese woman, age 21+, attractive and natural, same identity, modern Vietnamese lifestyle, realistic skin texture.'},
    pfTitleHint:{vi:'Phong cách Việt Nam cuốn hút mỗi ngày',en:'Captivating Vietnamese lifestyle every day'},
    pfOutfits:{vi:'áo crop top đen cổ vuông, quần short cạp cao màu kem, chất liệu mát và opaque\náo hai dây rib màu trắng ngà, quần short denim xanh nhạt\náo halter đỏ rượu vang, mini skort đen có quần trong kín đáo\náo sát nách hồng phấn, quần short be cạp cao\náo crop top tím lavender, quần short trắng\náo cổ vuông nâu chocolate, quần short màu kem\náo camisole olive nhạt, quần short đen cạp cao\náo off-shoulder coral, mini skort trắng kem\náo crop top vàng bơ, quần short nâu nhạt\náo sát nách xám than, quần short trắng\náo cổ yếm cam đất, quần short đen\náo crop top navy đậm, quần short be',en:'black square-neck crop top with cream high-waisted shorts\nivory rib camisole with light denim shorts\nburgundy halter top with black mini skort and safety shorts\nblush pink sleeveless top with beige high-waisted shorts\nlavender crop top with white shorts\nchocolate square-neck top with cream shorts\nlight olive camisole with black high-waisted shorts\ncoral off-shoulder top with cream mini skort\nbutter-yellow crop top with light brown shorts\ncharcoal sleeveless top with white shorts\nterracotta halter top with black shorts\ndark navy crop top with beige shorts'},
    pfBackgrounds:{vi:'phố đi bộ Nguyễn Huệ, Quận 1, TP.HCM\nBưu điện Trung tâm Sài Gòn, Quận 1, TP.HCM\nVinhomes Central Park với Landmark 81 phía sau, TP.HCM\nCầu Mống nhìn về Bitexco, TP.HCM\nHồ Hoàn Kiếm gần Tháp Rùa, Hà Nội\nđường Thanh Niên bên Hồ Tây, Hà Nội\nNhà thờ Lớn Hà Nội, khu phố cổ\nphố Tạ Hiện, Hoàn Kiếm, Hà Nội\nCầu Rồng bên sông Hàn, Đà Nẵng\nbãi biển Mỹ Khê, Đà Nẵng\nphố cổ Hội An, Quảng Nam\nQuảng trường Lâm Viên, Đà Lạt\nđường Trần Phú ven biển Nha Trang\nBến Ninh Kiều, Cần Thơ\nkhu vực Đại Nội Huế\nven biển Hạ Long, Quảng Ninh',en:'Nguyen Hue Walking Street, District 1, Ho Chi Minh City\nSaigon Central Post Office, District 1, Ho Chi Minh City\nVinhomes Central Park with Landmark 81, Ho Chi Minh City\nMong Bridge facing Bitexco, Ho Chi Minh City\nHoan Kiem Lake near Turtle Tower, Hanoi\nThanh Nien Road beside West Lake, Hanoi\nSt. Joseph Cathedral area, Hanoi Old Quarter\nTa Hien Street, Hoan Kiem, Hanoi\nDragon Bridge beside the Han River, Da Nang\nMy Khe Beach, Da Nang\nHoi An Ancient Town, Quang Nam\nLam Vien Square, Da Lat\nTran Phu beachfront road, Nha Trang\nNinh Kieu Wharf, Can Tho\nHue Imperial City area\nHa Long waterfront, Quang Ninh'},
    pfPoses:{vi:'đứng tự nhiên nhìn về máy quay\nđi bộ chậm trên phố rồi nhìn sang máy quay\ntựa nhẹ lan can, góc ba phần tư\nchỉnh tóc một lần rồi mỉm cười nhẹ\ncầm ly cà phê mang đi và bước chậm\nngồi ghế công viên với tư thế thư giãn\nselfie tự nhiên ở không gian lifestyle\nđi ngang qua máy quay rồi ngoái nhìn một lần\nđứng cạnh hàng cây hoặc mặt hồ, xoay nhẹ vai',en:'stand naturally and look toward camera\nwalk slowly along the street and glance toward camera\nlean lightly on a railing in a three-quarter angle\nadjust hair once and give a subtle smile\ncarry a takeaway coffee and walk slowly\nsit relaxed on a park bench\nnatural selfie in a lifestyle setting\nwalk past camera and glance back once\nstand by trees or a lake and turn shoulders slightly toward camera'}
  };

  const sourceByNode=new WeakMap();
  const attrSource=new WeakMap();
  const idx=()=>lang==='vi'?0:1;
  Object.assign(D,SETTINGS_EXTRA);

  function exact(text){const r=D[text];return r?r[idx()]:null}

  function dynamic(text){
    let out=String(text??'');
    if(lang==='vi'){
      const reps=[
        ['FRONTEND ','GIAO DIỆN '],['BACKEND ','BACKEND '],['major/minor không khớp','phiên bản chính/phụ không khớp'],
        ['models hiển thị','mô hình hiển thị'],['dùng được','dùng được'],['unsupported','không hỗ trợ'],['github','GitHub'],['soft','xóa mềm'],
        ['NO PERSONA','CHƯA CÓ PERSONA'],['persona OK','persona OK'],['master 2048 READY','bản chuẩn 2048 SẴN SÀNG'],['master chưa prep','chưa tạo bản chuẩn'],
        ['angles ','góc '],['scheduler OFF','lịch đăng TẮT'],['slots ','mốc '],['buf ','dự phòng '],['fallback','dự phòng'],['Title hint:','Gợi ý tiêu đề:'],['glam ','quyến rũ '],
        ['Queued ','Đã xếp hàng '],[' góc thiếu',' góc còn thiếu'],[' góc đã đang chạy',' góc đang chạy'],
        ['Saved ','Đã lưu '],['FRONT MASTER READY','FRONT CHUẨN SẴN SÀNG'],['persona saved','đã lưu persona'],['KHÔNG tự gen góc','KHÔNG tự tạo góc'],
        ['Persona prepared','Đã tạo FRONT chuẩn'],['MASTER 2048 READY','BẢN CHUẨN 2048 SẴN SÀNG'],['Prepared persona master:','Đã tạo persona chuẩn:'],
        ['Run ','Lượt '],[' final video queued',' video thành phẩm đã xếp hàng'],['mode=','chế độ='],['done=','hoàn tất='],['failed=','lỗi='],['active=','đang chạy='],
        [' done',' hoàn tất'],[' fail',' lỗi'],[' active',' đang chạy'],['scene ','cảnh '],['Publish job ','Tác vụ đăng '],
        ['Queue chưa có video.','Hàng đợi chưa có video.'],['warm-up: chờ đủ buffer trước khi đăng','khởi động: chờ đủ video dự phòng trước khi đăng'],
        ['buffer ','dự phòng '],['generating ','đang tạo '],['publishing ','đang đăng '],['random ±','ngẫu nhiên ±'],['INTERVAL','KHOẢNG CÁCH'],['DAILY SLOTS','MỐC GIỜ HẰNG NGÀY'],
        ['Next:','Tiếp theo:'],['Last:','Lần cuối:'],['Scheduler:','Lịch đăng:'],['Scheduler ON','Lịch đăng BẬT'],['Scheduler OFF','Lịch đăng TẮT'],
        ['AUTO TEST 1 PAGE','TỰ ĐỘNG KIỂM THỬ 1 TRANG'],['DRY RUN','CHẠY THỬ'],['PUBLISH THẬT','ĐĂNG THẬT'],
        ['Upload lỗi','Lỗi tải tệp'],['Page Profile','Hồ sơ Trang'],['SAVE PROFILE','LƯU HỒ SƠ'],['PREPARE PERSONA','TẠO FRONT CHUẨN'],['AUTO PUBLISH','TỰ ĐĂNG'],
        ['TEST VIDEO ONLY','CHỈ KIỂM THỬ VIDEO'],['TEST TOKEN','KIỂM TRA TOKEN'],['SYNC PAGES','ĐỒNG BỘ TRANG'],['Page đã xóa','Trang đã xóa'],
        ['Image model','Mô hình ảnh'],['Video model','Mô hình video'],['model nền','mô hình nền'],['model lỗi','mô hình lỗi'],['model','mô hình'],['request','yêu cầu'],
        ['clear','xóa'],['retest','kiểm tra lại'],['ignore','bỏ qua'],['Sync','Đồng bộ'],['Page','Trang']
      ];
      for(const [a,b] of reps) out=out.split(a).join(b);
      out=out.replace(/(\d+) Flow agent\b/g,'$1 tác nhân Flow');
    } else {
      const reps=[
        ['Chưa có Flow extension kết nối.','No Flow extension connected.'],['Tác nhân Flow','Flow agent'],['Tác vụ','Job'],['Phiên bản','Version'],['Trạng thái','Status'],
        ['Đã lưu ','Saved '],['Đã xếp hàng ','Queued '],['góc còn thiếu','missing angles'],['góc đang chạy','angles already running'],
        ['Đã tạo FRONT chuẩn','Persona prepared'],['BẢN CHUẨN 2048 SẴN SÀNG','MASTER 2048 READY'],['FRONT CHUẨN SẴN SÀNG','FRONT MASTER READY'],
        ['Lượt ','Run '],['hoàn tất=','done='],['lỗi=','failed='],['đang chạy=','active='],['Tác vụ đăng ','Publish job '],
        ['Hàng đợi chưa có video.','Queue has no video yet.'],['khởi động: chờ đủ video dự phòng trước khi đăng','warm-up: waiting for the buffer before publishing'],
        ['Tiếp theo:','Next:'],['Lần cuối:','Last:'],['Lịch đăng:','Scheduler:'],['CHẠY THỬ','DRY RUN'],['ĐĂNG THẬT','PUBLISH LIVE'],
        ['Lỗi tải tệp','Upload error'],['Hồ sơ Trang','Page Profile'],['ĐỒNG BỘ TRANG','SYNC PAGES'],['Trang đã xóa','deleted Page']
      ];
      for(const [a,b] of reps) out=out.split(a).join(b);
    }
    return out;
  }

  function t(key){return exact(key)??dynamic(key)}
  function status(key){return exact(String(key))??String(key)}
  function mode(key){return exact(String(key))??String(key)}
  function skip(node){
    const p=node.parentElement;
    return !p || p.closest('script,style,textarea,pre,.mono,#aiPreview');
  }
  function translateTextNode(node){
    if(skip(node))return;
    let src=sourceByNode.get(node);
    if(src==null){src=node.nodeValue;sourceByNode.set(node,src)}
    const trim=src.trim();if(!trim)return;
    const lead=src.match(/^\s*/)?.[0]||'',tail=src.match(/\s*$/)?.[0]||'';
    node.nodeValue=lead+t(trim)+tail;
  }
  function translateAttrs(el){
    if(!(el instanceof Element))return;
    let bag=attrSource.get(el);if(!bag){bag={};attrSource.set(el,bag)}
    for(const a of ['placeholder','title','aria-label','alt']){
      if(el.hasAttribute(a)){
        if(!(a in bag))bag[a]=el.getAttribute(a)||'';
        el.setAttribute(a,t(bag[a]));
      }
    }
    // Only translate read-only/disabled display values. Never mutate user-editable data.
    if(el instanceof HTMLInputElement && (el.disabled||el.readOnly) && el.type!=='file'){
      if(!('value' in bag))bag.value=el.value;
      const v=exact(bag.value);if(v!=null)el.value=v;
    }
  }
  function walk(root=document.body){
    if(!root)return;
    if(root.nodeType===Node.TEXT_NODE){translateTextNode(root);return}
    if(root.nodeType!==Node.ELEMENT_NODE&&root.nodeType!==Node.DOCUMENT_FRAGMENT_NODE)return;
    if(root instanceof Element)translateAttrs(root);
    const w=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
    let n;while((n=w.nextNode())){if(n.nodeType===Node.TEXT_NODE)translateTextNode(n);else translateAttrs(n)}
  }

  function applyDefaultFields(){
    for(const [id,pair] of Object.entries(DEFAULT_FIELDS)){
      const el=document.getElementById(id);if(!el)continue;
      const current=el.value;
      const isKnown=current===pair.vi||current===pair.en||el.dataset.i18nManaged==='1';
      if(!isKnown)continue;
      el.dataset.i18nManaged='1';
      el.value=lang==='vi'?pair.vi:pair.en;
      if(!el.dataset.i18nListener){el.dataset.i18nListener='1';el.addEventListener('input',()=>{el.dataset.i18nManaged='0'})}
    }
  }

  function addFileStyle(){
    if(document.getElementById('i18nFileStyle'))return;
    const style=document.createElement('style');style.id='i18nFileStyle';style.textContent=`
      input[type=file].i18n-file-native{display:none!important}
      .i18n-file-ui{display:flex;align-items:center;gap:8px;width:100%;min-height:40px}
      .i18n-file-ui button{flex:0 0 auto;border:1px solid #52617c;border-radius:7px;background:#25324a;color:#fff;padding:8px 11px;font-weight:700;cursor:pointer}
      .i18n-file-ui .i18n-file-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#aebbd1;font-size:12px}
    `;document.head.appendChild(style);
  }
  function enhanceFileInputs(){
    addFileStyle();
    document.querySelectorAll('input[type=file]:not([data-i18n-file])').forEach(input=>{
      input.dataset.i18nFile='1';input.classList.add('i18n-file-native');
      const wrap=document.createElement('div');wrap.className='i18n-file-ui';wrap.dataset.for=input.id;
      const btn=document.createElement('button');btn.type='button';
      const name=document.createElement('span');name.className='i18n-file-name';
      btn.addEventListener('click',()=>input.click());
      const update=()=>{btn.textContent=lang==='vi'?'CHỌN TỆP':'CHOOSE FILE';name.textContent=input.files?.[0]?.name||(lang==='vi'?'Chưa chọn tệp':'No file selected')};
      input.addEventListener('change',update);wrap.append(btn,name);input.insertAdjacentElement('afterend',wrap);update();
    });
    document.querySelectorAll('.i18n-file-ui').forEach(w=>{const input=document.getElementById(w.dataset.for);const btn=w.querySelector('button'),name=w.querySelector('.i18n-file-name');if(btn)btn.textContent=lang==='vi'?'CHỌN TỆP':'CHOOSE FILE';if(name&&!input?.files?.length)name.textContent=lang==='vi'?'Chưa chọn tệp':'No file selected'});
  }

  function apply(){
    document.documentElement.lang=lang;
    const sel=document.getElementById('uiLanguage');if(sel)sel.value=lang;
    walk(document.body);applyDefaultFields();enhanceFileInputs();
    document.dispatchEvent(new CustomEvent('ui-language-changed',{detail:{lang}}));
  }
  function setLanguage(value){lang=value==='en'?'en':'vi';localStorage.setItem(STORAGE_KEY,lang);apply()}

  window.UI_I18N={t,status,mode,dynamic,getLang:()=>lang,setLanguage,apply};
  window.setUiLanguage=setLanguage;
  const nativeAlert=window.alert.bind(window), nativeConfirm=window.confirm.bind(window);
  window.alert=(message)=>nativeAlert(dynamic(String(message??'')));
  window.confirm=(message)=>nativeConfirm(dynamic(String(message??'')));

  const pendingI18nNodes=new Set();let i18nFlushScheduled=false;
  function scheduleI18nFlush(){
    if(i18nFlushScheduled)return;i18nFlushScheduled=true;
    requestAnimationFrame(()=>{
      i18nFlushScheduled=false;
      for(const n of pendingI18nNodes)walk(n);
      pendingI18nNodes.clear();
      enhanceFileInputs();
    });
  }
  const observer=new MutationObserver(ms=>{
    for(const m of ms)for(const n of m.addedNodes)pendingI18nNodes.add(n);
    scheduleI18nFlush();
  });
  observer.observe(document.documentElement,{subtree:true,childList:true});
  document.addEventListener('DOMContentLoaded',()=>{
    const sel=document.getElementById('uiLanguage');if(sel)sel.addEventListener('change',e=>setLanguage(e.target.value));apply();
  });
})();
