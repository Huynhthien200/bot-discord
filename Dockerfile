# Dùng image python slim (nhẹ, phổ biến)
FROM python:3.12-slim

# Cài đặt các gói cần thiết cho pip install từ git
RUN apt-get update && apt-get install -y git

# Tạo thư mục app
WORKDIR /app

# Copy code và file cấu hình vào image
COPY . /app

# Tạo và kích hoạt virtualenv (không bắt buộc, python:3 base image đã có sẵn pip/env)
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Mặc định Railway sẽ dùng PORT từ env
EXPOSE 8080

# Lệnh chạy bot (thay bằng tên file chính nếu không phải main.py)
CMD ["python", "main.py"]
