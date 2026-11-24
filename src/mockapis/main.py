"""uvicorn 진입점: `uvicorn mockapis.main:app`"""

import os

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import ContainerClient
from mockapis.app import demo_app
from mockapis.consent import BlobConsentRegistry
app = demo_app(
    registry=BlobConsentRegistry(ContainerClient.from_container_url(
        os.environ["MOCK_AUTHORITY_URL"], credential=ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    )),
)
