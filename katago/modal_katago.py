# KataGo on a Modal GPU, same API as katago_server.py (POST /analyze, GET /health).
# Scales to zero: the GPU is billed only while a container is up (cold start ~20-30 s, then 2 min idle window).
# Callers need a Modal proxy token (Modal-Key / Modal-Secret headers).
#
#   modal deploy katago/modal_katago.py
#   KATAGO_URL=https://<workspace>--go-katago-serve.modal.run KATAGO_AUTH=modal KATAGO_MODAL_TOKEN=wk-...:ws-...

from pathlib import Path

import modal

GPU = 'L4'
KATAGO_ZIP = 'https://github.com/lightvector/KataGo/releases/download/v1.18.2/katago-v1.18.2-cuda12.8-cudnn9.8.0-linux-x64.zip'
NET = ('https://media.katagotraining.org/uploaded/networks/models/kata1/'
       'kata1-b18c384nbt-s9996604416-d4316597426.bin.gz')
HERE = Path(__file__).parent

image = (
    modal.Image.from_registry('nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04', add_python='3.11')
    .apt_install('curl', 'unzip')
    .run_commands(
        f'curl -fsSL -o /tmp/k.zip {KATAGO_ZIP} && unzip -q /tmp/k.zip katago -d /tmp && rm /tmp/k.zip',
        # an AppImage: extract it (no FUSE in containers)
        'cd /tmp && chmod +x katago && ./katago --appimage-extract >/dev/null && mv squashfs-root /opt/katago && rm katago',
        f'curl -fsSL -o /opt/katago/model.bin.gz {NET}',
    )
    .pip_install('fastapi[standard]')
    .add_local_file(HERE / 'analysis.cfg', '/opt/katago/analysis.cfg', copy=True)
    # the local config is tuned for a GTX 1650; tensor-core GPUs want FP16
    .run_commands("sed -i 's/^cudaUseFP16 = false/cudaUseFP16 = auto/; s/^cudaUseNHWC = false/cudaUseNHWC = auto/' "
                  '/opt/katago/analysis.cfg')
    .env({'KATAGO_BIN': '/opt/katago/AppRun', 'KATAGO_MODEL': '/opt/katago/model.bin.gz',
          'KATAGO_CONFIG': '/opt/katago/analysis.cfg'})
    .add_local_file(HERE / 'katago_server.py', '/root/katago_server.py')
)

app = modal.App('go-katago')


@app.cls(gpu=GPU, image=image, min_containers=0, max_containers=1, scaledown_window=120, timeout=120)
@modal.concurrent(max_inputs=8)
class KataGo:
    @modal.enter()
    def start(self):
        import katago_server   # starts the KataGo process and its reader thread
        self.engine = katago_server

    @modal.asgi_app(requires_proxy_auth=True)
    def serve(self):
        from fastapi import Body, FastAPI, HTTPException

        web = FastAPI()

        @web.get('/health')
        def health():
            return {'status': 'ok' if self.engine.proc.poll() is None else 'down'}

        @web.post('/analyze')
        def analyze(req: dict = Body(...)):
            try:
                return self.engine.analyze(req)
            except (ValueError, KeyError, TypeError) as ex:
                raise HTTPException(400, str(ex))

        return web
