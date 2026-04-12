import logging
import os
from pathlib import Path

from quart import Quart, redirect, render_template, request, session, url_for

try:
    from .weather_api import weather_bp
except ImportError:
    from weather_api import weather_bp

app = Quart(__name__)
app.register_blueprint(weather_bp)

logger = logging.getLogger(__name__)

VERSION_PATH = Path(__file__).resolve().parent.parent / "VERSION"
if not VERSION_PATH.exists():
    VERSION_PATH = Path("VERSION")
VERSION = VERSION_PATH.read_text().strip() if VERSION_PATH.exists() else "0.0.0"
DEPLOY_DATE = os.environ.get("DEPLOY_DATE", "unknown")


@app.route("/")
async def index():
    return await render_template("index.html", version=VERSION, deploy_date=DEPLOY_DATE)


if __name__ == "__main__":
    import uvicorn

    logger.info("weather v%s (deployed %s)", VERSION, DEPLOY_DATE)
    port = int(os.environ["PORT"])
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
