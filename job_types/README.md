# V2.8 Job Type plugin contract

Thêm niche mới không sửa `master/app.py`:

1. Tạo `job_types/<slug>/manifest.json`.
2. Tạo `job_types/<slug>/adapter.py` với class `Adapter` có `prepare`, `start`, `wait`.
3. Restart V2.8. Core tự scan plugin và cấp clone ID `<template>.<n>`.

`start()` chỉ tạo nội dung/video. Facebook publish luôn do core V2.8 xử lý để một video có thể đi nhiều Page mà không render lại.
