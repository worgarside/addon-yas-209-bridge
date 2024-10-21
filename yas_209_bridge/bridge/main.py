"""Custom addon to listen to the YAS-209 and post updates to HA."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO, StringIO
from json import dumps
from logging import DEBUG, getLogger
from os import environ, getenv
from typing import Any, TypedDict

from paramiko import AutoAddPolicy, RSAKey, SFTPClient, SSHClient
from requests import post
from wg_utilities.devices.yamaha_yas_209 import YamahaYas209
from wg_utilities.devices.yamaha_yas_209.yamaha_yas_209 import CurrentTrack
from wg_utilities.exceptions import on_exception
from wg_utilities.loggers import add_stream_handler

LOGGER = getLogger(__name__)
LOGGER.setLevel(DEBUG)
add_stream_handler(LOGGER)


class PayloadInfo(TypedDict):
    """Info for the PAYLOAD constant."""

    state: str | None
    album_art_uri: str | None
    volume_level: float | None
    media_duration: float | None
    media_title: str | None
    media_artist: str | None
    media_album_name: str | None


TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

SFTP_HOSTNAME = getenv("SFTP_HOSTNAME", "null")  # Default to satisfy mypy
SFTP_USERNAME = getenv("SFTP_USERNAME")
SFTP_PRIVATE_KEY_STRING = getenv("SFTP_PRIVATE_KEY_STRING")

SFTP_PRIVATE_KEY = (
    RSAKey.from_private_key(StringIO(SFTP_PRIVATE_KEY_STRING))
    if SFTP_PRIVATE_KEY_STRING
    else None
)

# Env vars come through as "null" within the Docker container for some reason
SFTP_CREDS_PROVIDED = all(
    v not in (None, "null")
    for v in (
        SFTP_HOSTNAME,
        SFTP_USERNAME,
        SFTP_PRIVATE_KEY,
    )
)


def create_sftp_client() -> SFTPClient:
    # TODO: only instantiate if the connection is closed, this is grim.
    """Create a new SFTP client instance.

    This isn't just a constant client as the socket closes after some time, so we just
    create a new client per request.

    Returns:
        SFTPClient: a new SFTP client
    """

    ssh = SSHClient()
    ssh.set_missing_host_key_policy(AutoAddPolicy())

    ssh.connect(
        hostname=SFTP_HOSTNAME,
        username=SFTP_USERNAME,
        pkey=SFTP_PRIVATE_KEY,
    )
    sftp_client = ssh.open_sftp()

    return sftp_client


@on_exception(raise_after_callback=False, logger=LOGGER)
def log_request_payload(event_payload: dict[str, Any]) -> None:
    """Log the request payload locally (to HA) for archiving etc.

    Args:
        event_payload (dict): the payload received from the YAS-209
    """

    if (last_change := event_payload.get("last_change")) is not None:
        event_payload["last_change"] = last_change.dict()

    event_timestamp = event_payload.get("timestamp", datetime.now())

    file_path = event_timestamp.strftime("config/.addons/yas_209_bridge/%Y/%m/%d")
    file_name = event_timestamp.strftime("payload_%Y%m%d%H%M%S%f.json")

    sftp_client = create_sftp_client()

    sftp_client.chdir("/")
    for dir_ in file_path.split("/"):
        try:
            sftp_client.chdir(dir_)
        except FileNotFoundError:
            sftp_client.mkdir(dir_)
            sftp_client.chdir(dir_)

    sftp_client.putfo(
        BytesIO(dumps(event_payload, indent=2, default=str).encode()), file_name
    )

    LOGGER.debug(
        "Logged %s payload to '%s/%s'",
        event_payload.get("service_id", "unknown").split(":")[-1],
        file_path,
        file_name,
    )

    sftp_client.close()


@on_exception(raise_after_callback=False, logger=LOGGER)
def pass_data_to_home_assistant() -> None:
    """Send the payload to HA.

    The payload is global as we don't want to remove track metadata on a volume update,
    for example. The data should persist unless explicitly removed.
    """

    res = post(
        "http://homeassistant.local:8123/api/webhook/wills_yas_209_bridge_input",
        json=PAYLOAD,
        headers={"Content-Type": "application/json"},
        timeout=5,
    )

    LOGGER.debug("Webhook response: %i %s", res.status_code, res.reason)

    res.raise_for_status()


def on_volume_update(volume: float) -> None:
    """Process volume updates.

    Args:
        volume (float): the new volume level
    """

    LOGGER.debug("Volume updated to %f", volume)

    PAYLOAD["volume_level"] = volume

    pass_data_to_home_assistant()


def on_state_update(state: str) -> None:
    """Process state updates.

    Args:
        state (str): the new state
    """
    LOGGER.debug("State updated to %s", state)

    PAYLOAD["state"] = state

    pass_data_to_home_assistant()


def on_track_update(track: CurrentTrack.Info) -> None:
    """Process track updates.

    Args:
        track (CurrentTrack.Info): the track metadata
    """
    LOGGER.debug("Track updated to %s", dumps(track, default=str))

    PAYLOAD.update(track)  # type: ignore[typeddict-item]

    pass_data_to_home_assistant()


PAYLOAD: PayloadInfo = {
    "state": None,
    "album_art_uri": None,
    "volume_level": None,
    "media_duration": None,
    "media_title": None,
    "media_artist": None,
    "media_album_name": None,
}


if not SFTP_CREDS_PROVIDED:
    LOGGER.warning(
        "SFTP credentials not provided. Payloads will not be logged to the SFTP server"
    )

YAS_209 = YamahaYas209(
    environ["YAS_209_IP"],
    on_event=log_request_payload if SFTP_CREDS_PROVIDED else None,
    on_volume_update=on_volume_update,
    on_state_update=on_state_update,
    on_track_update=on_track_update,
    start_listener=True,
    listen_ip=(
        None
        if (listen_ip := getenv("LISTEN_IP", "null").lower()) == "null"
        else listen_ip
    ),
    listen_port=(
        None
        if (listen_port := getenv("LISTEN_PORT", "null").lower()) == "null"
        else int(listen_port)
    ),
    source_port=(
        None
        if (source_port := getenv("SOURCE_PORT", "null").lower()) == "null"
        else int(source_port)
    ),
)


PAYLOAD["state"] = YAS_209.state.value
PAYLOAD["volume_level"] = YAS_209.volume_level
