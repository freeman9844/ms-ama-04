"""uvicorn 진입점: `uvicorn mockapis.main:app`"""

from datetime import datetime

from mockapis.app import create_app
from mockapis.demo import KST, build_demo_cases, load_secret, resolve_as_of

app = create_app(build_demo_cases(resolve_as_of()), load_secret(), lambda: datetime.now(KST))
