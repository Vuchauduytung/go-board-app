# Go Scan trên Oracle Cloud Always Free — hướng dẫn từ đầu

Một VM ARM miễn phí, luôn bật (không chờ khởi động như Cloud Run). Từ 7/2026 Oracle chỉ cho miễn phí **1.500 giờ
OCPU + 9.000 GB-giờ RAM mỗi tháng**, tức một VM **2 OCPU / 12 GB** chạy cả tháng, cộng 200 GB ổ đĩa.

| Thành phần | Chạy ở |
|---|---|
| Web + nhận dạng ảnh + trợ lý + review SGF | container `app` (cùng `Dockerfile` với Cloud Run, ~4 GB RAM) |
| Lịch sử chat, hạn mức, cache KataGo | container `valkey` |
| KataGo | Modal GPU L4 (khuyến nghị; tốn credit Modal khi dùng) hoặc CPU của VM (profile `katago`, chậm) |
| HTTPS + đăng nhập Google | **DuckDNS + Caddy + oauth2-proxy** (miễn phí, mặc định) hoặc Cloudflare Tunnel + Access (cần tên miền riêng) |
| Sách (vector), AI | Qdrant Cloud, Gemini (+ các nhà cung cấp dự phòng, xem `deploy/LLM_PROVIDERS.md`) |

## Có cần mua tên miền không?

Không. Có ba cách miễn phí:

| Cách | Địa chỉ | Ghi chú |
|---|---|---|
| **DuckDNS** (hướng dẫn này) | `ten-ban.duckdns.org` | Có ngay, tối đa 5 tên. Chạy trên hạ tầng quyên góp, thỉnh thoảng chậm hoặc gián đoạn DNS. Mở cổng 80/443 trên VM. |
| eu.org | `ten-ban.eu.org` | Tên miền thật, đưa được lên Cloudflare (dùng profile `cloudflare`); phải chờ duyệt (vài ngày tới vài tuần). |
| DigitalPlat FreeDomain | `ten-ban.dpdns.org`… | Đăng ký qua GitHub, đưa được lên Cloudflare. |

Mua tên miền (~10 USD/năm, ví dụ Cloudflare Registrar) chỉ cần khi muốn địa chỉ đẹp hoặc dùng Cloudflare Access.

## 1. Tạo tài khoản Oracle Cloud

1. Vào https://signup.cloud.oracle.com → điền email, quốc gia Việt Nam, xác minh email.
2. **Home region**: chọn một lần, không đổi được, tài nguyên miễn phí chỉ có ở đó. Singapore (`ap-singapore-1`) gần
   Việt Nam; nếu sau này hay báo hết máy (Out of capacity) thì các vùng ít người hơn dễ tạo hơn.
3. Nhập thẻ Visa/Mastercard để xác minh (Oracle giữ tạm khoảng 1 USD rồi hoàn). Thẻ trả trước / ảo hay bị từ chối.
4. Chờ email "Your account is ready" (vài phút tới vài giờ), đăng nhập https://cloud.oracle.com.
5. **Nâng lên Pay As You Go** (khuyến nghị): menu ☰ → Billing & Cost Management → Upgrade and Manage Payment →
   Pay As You Go. Vẫn miễn phí trong hạn mức Always Free, nhưng:
   - VM không bị thu hồi khi "nhàn rỗi" (tài khoản free bị thu hồi nếu 7 ngày liền CPU, mạng và RAM đều < 20%,
     mà app ít người dùng thì dễ rơi vào);
   - dễ tạo được máy A1 hơn.
6. Ngay sau đó tạo **Budget**: Billing → Budgets → Create Budget, 1 USD/tháng, cảnh báo email khi đạt 1%. Nếu lỡ tạo
   thứ tính tiền, bạn biết ngay.

## 2. Tạo VM

1. ☰ → Compute → Instances → **Create instance**.
2. Image: **Canonical Ubuntu 24.04** (bấm Change image → Ubuntu; chọn bản *aarch64*).
3. Shape: Change shape → Ampere → **VM.Standard.A1.Flex**, **2 OCPU, 12 GB**.
4. Networking: để mặc định (tạo VCN mới, **Assign a public IPv4 address**).
5. SSH keys: *Generate a key pair for me* → **tải cả private key** (hoặc dán public key của bạn).
6. Boot volume: 100 GB.
7. Create. Báo "Out of host capacity" thì đổi Availability Domain, hoặc thử lại vào giờ khác.
8. Ghi lại **Public IP address** ở trang chi tiết VM.

### Mở cổng 80/443 (cho HTTPS)

1. Trang VM → Primary VNIC → Subnet → Security List mặc định → **Add Ingress Rules**: Source `0.0.0.0/0`, TCP,
   Destination port `80,443`.
2. Ubuntu trên Oracle có tường lửa riêng, mở thêm trên VM (bước 3 bên dưới).

## 3. Cài đặt trên VM

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<IP>

# tường lửa của image Oracle: cho phép 80/443 và lưu lại
sudo iptables -I INPUT 6 -p tcp -m state --state NEW -m multiport --dports 80,443 -j ACCEPT
sudo netfilter-persistent save
# Docker
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker ubuntu && exit
```

Đăng nhập lại rồi lấy code và mô hình:

```bash
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<IP>
git clone https://github.com/Vuchauduytung/go-board-app.git && cd go-board-app   # repo private: dùng deploy key hoặc rsync
MODELS_ONLY=1 scripts/fetch-assets.sh
sudo mkdir -p /srv/go-scan && sudo chown ubuntu /srv/go-scan
```

Ảnh trang sách (~135 MB, có bản quyền, không có trong git), chạy **trên máy bạn**:

```bash
rsync -a -e "ssh -i ~/Downloads/ssh-key-XXX.key" books/pages/ ubuntu@<IP>:go-board-app/books/pages/
```

## 4. Tên miền DuckDNS

1. https://www.duckdns.org → đăng nhập bằng Google/GitHub.
2. Gõ tên (ví dụ `goscan-tung`) → **add domain** → ô *current ip* điền Public IP của VM → **update ip**.
3. Địa chỉ của bạn là `goscan-tung.duckdns.org`. IP public của VM Oracle không đổi khi VM còn tồn tại, nên không
   cần cập nhật tự động.

## 5. Đăng nhập Google (OAuth client cho oauth2-proxy)

Dùng luôn project Google Cloud hiện có (đã có màn hình đồng ý OAuth "Go Scan IAP" ở chế độ Testing):

1. https://console.cloud.google.com → APIs & Services → **Credentials** → Create credentials → **OAuth client ID**.
2. Application type: **Web application**, tên `Go Scan Oracle`.
3. Authorized redirect URIs: `https://goscan-tung.duckdns.org/oauth2/callback`.
4. Create → ghi lại **Client ID** và **Client secret**.
5. OAuth consent screen → **Test users**: thêm email của những người được dùng (chế độ Testing tối đa 100 người).

## 6. Cấu hình và chạy

```bash
cd ~/go-board-app
cp deploy/oracle/.env.example deploy/oracle/.env
cp deploy/oracle/emails.txt.example deploy/oracle/emails.txt   # danh sách email được đăng nhập, mỗi dòng một email
openssl rand -base64 32 | tr -- '+/' '-_'                        # dán vào COOKIE_SECRET
nano deploy/oracle/.env    # DOMAIN, GOOGLE_CLIENT_ID/SECRET, COOKIE_SECRET, GEMINI_API_KEY, QDRANT_*, KATAGO_MODAL_TOKEN
docker compose -f deploy/oracle/compose.yaml --env-file deploy/oracle/.env up -d --build
docker compose -f deploy/oracle/compose.yaml --env-file deploy/oracle/.env logs -f app caddy
```

Lần build đầu mất 15–25 phút (cài torch cho ARM). Mở `https://goscan-tung.duckdns.org` → đăng nhập Google → góc
trên bên phải hiện tên bạn và "admin".

Người dùng mới: thêm email vào `deploy/oracle/emails.txt` **và** Test users (bước 5), rồi
`docker compose -f deploy/oracle/compose.yaml --env-file deploy/oracle/.env restart oauth2-proxy`.

### Muốn dùng Cloudflare thay DuckDNS

Khi đã có tên miền trên Cloudflare (mua, eu.org hoặc DigitalPlat): Zero Trust → Networks → Tunnels → tạo tunnel,
Public hostname `go.<tên miền>` → `http://app:8000`; Access → Applications → Self-hosted cho hostname đó, policy
Allow theo email. Trong `.env`: `COMPOSE_PROFILES=cloudflare`, `TRUST_FORWARDED_EMAIL=0`, `TRUST_CF_ACCESS=1`,
`CF_TUNNEL_TOKEN=...`. Có thể đóng lại cổng 80/443.

**Chỉ bật một** trong `TRUST_FORWARDED_EMAIL` / `TRUST_CF_ACCESS`, đúng với proxy đang dùng: app tin header đăng nhập
của proxy đó.

## Mang dữ liệu từ Cloud Run sang (tuỳ chọn)

```bash
gcloud storage rsync -r gs://<project>-go-scan/sessions /tmp/go-scan/sessions
gcloud storage rsync -r gs://<project>-go-scan/scans /tmp/go-scan/scans
rsync -a -e "ssh -i ~/Downloads/ssh-key-XXX.key" /tmp/go-scan/ ubuntu@<IP>:/srv/go-scan/
```

Ván cờ được lưu theo email, nên cùng tài khoản Google sẽ thấy lại ván của mình.

## Vận hành

- Cập nhật: `git pull && docker compose -f deploy/oracle/compose.yaml --env-file deploy/oracle/.env up -d --build`.
- Sao lưu: dữ liệu ở `/srv/go-scan`. Bật **backup policy** cho boot volume (Oracle cho 5 bản backup miễn phí).
- Debug không qua đăng nhập: `ssh -L 8765:localhost:8765 ...` rồi mở `http://localhost:8765` (bạn là người dùng khách).
- Bản vá bảo mật: Ubuntu tự cài (`unattended-upgrades`); thỉnh thoảng `sudo apt update && sudo apt upgrade && sudo reboot`.
