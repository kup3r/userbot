# Local phone login

The web server listens on `0.0.0.0:$PORT` (default `10000`). `0.0.0.0` is a bind address, not an address a phone should open.

## Recommended local test

1. Start the service:
   `python main.py`
2. Run `ipconfig` and find the PC's IPv4 address, for example `192.168.1.100`.
3. Put this in `.env`:
   `PUBLIC_BASE_URL=http://192.168.1.100:10000`
4. If Windows Firewall asks, allow Python/Uvicorn on the private network, or create an inbound TCP rule for port 10000.
5. From the phone, while on the same Wi-Fi, open:
   `http://192.168.1.100:10000/health`
6. When that works, use `/connect_phone` in Control Bot. The bot link will point to the same base URL.

## Automatic LAN URL

Set `AUTO_LOCAL_URL=true` and leave `PUBLIC_BASE_URL` empty. The service will best-effort detect the PC's LAN address and build `http://<LAN-IP>:<PORT>`.

This is for local testing only. For real users use HTTPS on Render or another public reverse proxy/tunnel.

## Render

Set `PUBLIC_BASE_URL=https://YOUR-SERVICE.onrender.com`, or leave it empty if the platform exposes `RENDER_EXTERNAL_URL` and the app can read it.
