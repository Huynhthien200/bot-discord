# 🪙 SUI Discord Bot (Auto Withdraw)

Bot Discord tự động kiểm tra số dư các ví SUI, gửi cảnh báo khi có thay đổi và **rút tiền** nếu ví chính nhận được SUI.

## ⚙️ Env cần thiết:

- DISCORD_TOKEN
- DISCORD_CHANNEL_ID
- SUI_PRIVATE_KEY
- SUI_TARGET_ADDRESS
- WATCHED_ADDRESSES: ví dụ `{"ví chính": "0x...", "ví phụ": "0x..."}`
- RPC_URL (tuỳ chọn, mặc định mainnet)
- POLL_INTERVAL (s, mặc định 1s)

## 🚀 Deploy:

1. Fork hoặc tạo repo từ template này.
2. Deploy trên [Render.com](https://render.com/) → chọn `render.yaml`
3. Thêm env vars tương ứng.
