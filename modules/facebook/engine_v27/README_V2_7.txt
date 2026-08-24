BROLL V2.7 - ROBUST STOCK DOWNLOADER

Fix lỗi Pexels SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC.

- Retry HTTP 4 lần.
- Resume file .part bằng Range nếu server hỗ trợ.
- Chunk nhỏ 256KB để giảm lỗi TLS trên kết nối không ổn định.
- Fallback curl.exe với --retry-all-errors nếu requests vẫn lỗi.
- Nếu 1 Pexels candidate tải hỏng, bỏ candidate đó và lấy clip khác.
- Ưu tiên Pexels 720p/1080p thay vì 4K vì output chỉ 1080x1080.
- Giữ nguyên V2.6: voice Trung, sub Việt, celebrity mute.

Dán đè app.py vào folder cũ. Không xóa .env hoặc .venv.
