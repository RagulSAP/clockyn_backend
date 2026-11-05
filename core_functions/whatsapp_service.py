import os
import logging
import requests
from dotenv import load_dotenv
from utils import branding

load_dotenv()
logger = logging.getLogger(__name__)


def _clean_digits(value: str) -> str:
    return ''.join(ch for ch in str(value) if ch.isdigit())


def _build_employee_onboard_payload(mobile: str, name: str, org: str):
    """
    Build the MSG91 outbound template payload for the onboarding invitation.

    MSG91 template placeholders are positional, so the order here must match
    the template body:
    var_1 -> employee name
    var_2 -> organization name
    var_3 -> app download link
    """
    mobile_clean = _clean_digits(mobile)
    if not mobile_clean.startswith("91"):
        mobile_clean = "91" + mobile_clean

    integrated_number_clean = _clean_digits(os.getenv("MSG91_WHATSAPP_NUMBER", ""))
    # No built-in default: the template must be approved at the provider under the current product name.
    template_name = branding.whatsapp_template()
    app_link = branding.app_download_link()

    return {
        "integrated_number": integrated_number_clean,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": "en",
                    "policy": "deterministic"
                },
                "to_and_components": [
                    {
                        "to": [mobile_clean],
                        "components": {
                            "body_var_1": {"type": "text", "value": name},
                            "body_var_2": {"type": "text", "value": org},
                            "body_var_3": {"type": "text", "value": app_link}
                        }
                    }
                ]
            }
        }
    }


def send_employee_onboard_whatsapp(mobile: str, name: str, org: str):
    """
    Send WhatsApp onboarding message to newly added employee via MSG91.
    var_1 = employee name, var_2 = org name, var_3 = play store link
    Mobile must include country code e.g. 919876543210
    """
    auth_key = os.getenv("MSG91_WHATSAPP_AUTH_KEY")
    integrated_number = os.getenv("MSG91_WHATSAPP_NUMBER")
    payload = _build_employee_onboard_payload(mobile, name, org)
    mobile_clean = payload["payload"]["template"]["to_and_components"][0]["to"][0]
    integrated_number_clean = _clean_digits(integrated_number)
    template_name = payload["payload"]["template"]["name"]

    if not template_name:
        logger.warning(
            "[WA SKIP] MSG91_WHATSAPP_TEMPLATE is not set - invitation to %s not sent", mobile_clean
        )
        return False
    app_link = payload["payload"]["template"]["to_and_components"][0]["components"]["body_var_3"]["value"]
    if not app_link:
        logger.warning(
            "[WA SKIP] no app link configured (ANDROID_STORE_URL / IOS_STORE_URL / WEBSITE_URL) - invitation to %s not sent",
            mobile_clean,
        )
        return False

    if not auth_key or not integrated_number_clean:
        logger.warning(
            "[WA FALLBACK] WhatsApp configuration incomplete "
            f"(auth_key={bool(auth_key)}, integrated_number={bool(integrated_number_clean)}). Would have sent to {mobile_clean}"
        )
        return False

    url = "https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/"
    payload["integrated_number"] = integrated_number_clean

    headers = {
        "authkey": auth_key,
        "accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        logger.info(f"Sending WhatsApp onboarding message to {mobile_clean} with template {template_name}")
        logger.info(f"WhatsApp payload: {payload}")
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        try:
            result = response.json()
        except ValueError:
            result = {"raw_response": response.text}

        if not response.ok:
            logger.error(
                f"WhatsApp send failed for {mobile_clean} | status={response.status_code} | response={result}"
            )
            return False

        logger.info(f"WhatsApp onboard sent to {mobile_clean}: {result}")
        return True
    except Exception as e:
        logger.error(f"WhatsApp send failed for {mobile_clean}: {str(e)}")
        return False
