# Deploying the 3-Agent Support Desk to a VPS with Docker

Package contents:
```
app.py                  - the Streamlit + AutoGen app
requirements.txt        - Python dependencies (installed inside the image)
Dockerfile                - builds the app image
docker-compose.yml        - builds + runs the container, persists ticket data
.dockerignore             - keeps .env / venv / data out of the image
.env.example               - copy to .env and fill in your key
nginx.conf                  - optional reverse proxy so the app is on port 80/443
data/                        - empty folder; tickets.txt will be persisted here
DOCKER_DEPLOY.md              - this file
```

No systemd unit needed this time — Docker's own `restart: unless-stopped` policy
keeps the container running across crashes and VPS reboots.

---

## 1. Provision and connect to the VPS

```bash
ssh root@YOUR_VPS_IP
adduser deploy
usermod -aG sudo deploy
su - deploy
```

## 2. Install Docker and Docker Compose

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker          # refresh group membership without logging out
docker --version
docker compose version  # comes bundled with modern Docker installs
```

## 3. Upload the project

```bash
scp -r ./docker-package deploy@YOUR_VPS_IP:/home/deploy/support-app
```
(Or `git clone` your repo. Make sure `.env` and `data/` stay out of git — they're
already listed in `.dockerignore`, add them to `.gitignore` too.)

## 4. Set your API key

```bash
cd /home/deploy/support-app
cp .env.example .env
nano .env        # paste your real OPENAI_API_KEY, save and exit
chmod 600 .env
```

## 5. Build and run

```bash
docker compose up -d --build
```
This builds the image, starts the container in the background, and publishes
it on port 8501. `./data` on the host is mounted into the container so
`tickets.txt` survives rebuilds and restarts.

## 6. Verify it's running

```bash
docker compose ps
docker compose logs -f
```
Visit `http://YOUR_VPS_IP:8501` in a browser. If your firewall blocks it, temporarily allow it for this test:
```bash
sudo ufw allow 8501
```

## 7. Put nginx in front of it (recommended)

nginx runs on the host (not in Docker) and proxies to the container's published port:
```bash
sudo apt update && sudo apt install -y nginx
sudo cp nginx.conf /etc/nginx/sites-available/support-app
sudo nano /etc/nginx/sites-available/support-app   # set your real domain/IP
sudo ln -s /etc/nginx/sites-available/support-app /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## 8. Firewall

```bash
sudo ufw allow 'Nginx Full'   # opens 80 and 443
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw deny 8501             # close direct access now nginx is fronting it
```

## 9. (Optional) HTTPS with Let's Encrypt

Only if you have a real domain pointed at the VPS:
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 10. Verify end to end

Visit `http://your-domain.com` (or `https://` after step 9). Submit a test
query and confirm both answers, sources, and a new ticket in the sidebar all
appear, then check the file landed on the host:
```bash
cat /home/deploy/support-app/data/tickets.txt
```

---

## Everyday operations

```bash
docker compose logs -f              # live logs
docker compose restart              # restart without rebuilding
docker compose down                 # stop and remove the container (data/ is kept)
docker compose up -d --build         # pull code changes, rebuild, redeploy
docker compose exec support-app sh   # shell into the running container
```

## Updating the app later

```bash
cd /home/deploy/support-app
git pull                      # or re-upload changed files (app.py, requirements.txt, etc.)
docker compose up -d --build   # rebuilds the image and replaces the container
```
`./data/tickets.txt` is untouched by this since it lives outside the image, on the host.

## Notes

- The container runs as a non-root `appuser`, and the image includes a
  `HEALTHCHECK` that Docker (and `docker compose ps`) will report on.
- Ticket history lives at `./data/tickets.txt` on the host — back it up
  periodically if it matters long-term; it's a flat file, not a database.
- Rotate `OPENAI_API_KEY` immediately if `.env` is ever exposed or committed.
- To run multiple isolated instances on one VPS (e.g. staging + prod), copy
  the whole folder, change the host port in `docker-compose.yml`
  (`"8502:8501"`), and give the container a different `container_name`.
