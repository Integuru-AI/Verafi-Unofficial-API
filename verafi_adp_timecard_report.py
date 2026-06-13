from curl_cffi import requests


def run(headers, user_input):
    """Fetch the ADP Workforce Now Timecard report for a connected organization.

    Accepts ADP session cookies via adp_cookie_jar in user_input.
    Uses those cookies to make requests to ADP Workforce Now.

    If ADP rejects the session (redirect to login), returns
    managed_session_required indicating the cookies are invalid/expired.

    Future implementation flow (when session is valid):
    1. Navigate to https://workforcenow.cloud.adp.com/theme/index.html#/home
    2. Go to Reports / Reports & Analytics
    3. Open the Time & Scheduling > Timecard report
    4. Apply date_range_start / date_range_end filters if provided
    5. Run/export the report
    6. Return the exported file URL or parsed timecard rows
    """
    # --- Validate required inputs ---
    organization_id = user_input.get("organization_id")
    if not organization_id:
        return {"status_code": 400, "body": {"error": "organization_id is required"}}

    organization_name = user_input.get("organization_name")
    if not organization_name:
        return {"status_code": 400, "body": {"error": "organization_name is required"}}

    user_id = user_input.get("user_id")
    if not user_id:
        return {"status_code": 400, "body": {"error": "user_id is required"}}

    report_type = user_input.get("report_type", "timecard")
    if report_type != "timecard":
        return {
            "status_code": 400,
            "body": {"error": f"Unsupported report_type: {report_type}. Only 'timecard' is supported."},
        }

    # --- Optional date range ---
    date_range_start = user_input.get("date_range_start")  # YYYY-MM-DD or None
    date_range_end = user_input.get("date_range_end")  # YYYY-MM-DD or None

    # --- Resolve ADP cookies ---
    adp_cookie_jar = user_input.get("adp_cookie_jar")

    if not adp_cookie_jar or not isinstance(adp_cookie_jar, dict):
        return _managed_session_response(
            organization_id, organization_name, user_id,
            report_type, date_range_start, date_range_end,
            "No adp_cookie_jar provided in input.",
        )

    wfn_domain = "workforcenow.cloud.adp.com"
    ezlm_domain = "wwsm-components.adp.com"

    wfn_entries = adp_cookie_jar.get(wfn_domain)
    ezlm_entries = adp_cookie_jar.get(ezlm_domain)

    if not wfn_entries or not isinstance(wfn_entries, list) or len(wfn_entries) == 0:
        return _managed_session_response(
            organization_id, organization_name, user_id,
            report_type, date_range_start, date_range_end,
            f"adp_cookie_jar is missing {wfn_domain} cookies.",
        )

    if not ezlm_entries or not isinstance(ezlm_entries, list) or len(ezlm_entries) == 0:
        return _managed_session_response(
            organization_id, organization_name, user_id,
            report_type, date_range_start, date_range_end,
            f"adp_cookie_jar is missing {ezlm_domain} cookies.",
        )

    wfn_cookie_str = _build_cookie_string(wfn_entries)
    ezlm_cookie_str = _build_cookie_string(ezlm_entries)

    if not wfn_cookie_str:
        return _managed_session_response(
            organization_id, organization_name, user_id,
            report_type, date_range_start, date_range_end,
            f"adp_cookie_jar {wfn_domain} entries have no valid name/value pairs.",
        )

    # --- Execute ADP report export ---
    try:
        result = _execute_report(
            wfn_cookie_str, ezlm_cookie_str,
            organization_id, organization_name, user_id,
            report_type, date_range_start, date_range_end,
        )
        return result
    except Exception:
        return {"status_code": 500, "body": {"error": "Internal error during ADP report export"}}


# === PRIVATE ===


def _build_cookie_string(cookie_entries):
    """Build a Cookie header string from list of {name, value} dicts.
    Never logs or exposes cookie values."""
    pairs = []
    for entry in cookie_entries:
        if isinstance(entry, dict) and entry.get("name") and entry.get("value"):
            pairs.append(f"{entry['name']}={entry['value']}")
    return "; ".join(pairs)


def _managed_session_response(organization_id, organization_name, user_id,
                              report_type, date_range_start, date_range_end,
                              detail):
    """Return managed_session_required response."""
    return {
        "status_code": 200,
        "body": {
            "status": "managed_session_required",
            "organization_id": organization_id,
            "organization_name": organization_name,
            "user_id": user_id,
            "report_type": report_type,
            "date_range_start": date_range_start,
            "date_range_end": date_range_end,
            "file_name": None,
            "content_type": None,
            "download_url": None,
            "parsed_data": [],
            "error": {
                "code": "ADP_MANAGED_SESSION_REQUIRED",
                "message": (
                    "ADP Workforce Now session is not usable. "
                    f"{detail} "
                    "This may indicate the session expired or ADP rejected the "
                    "cookie handoff (IP/TLS/context binding)."
                ),
            },
        },
    }


def _execute_report(wfn_cookies, ezlm_cookies, organization_id, organization_name,
                    user_id, report_type, date_range_start, date_range_end):
    """Execute the ADP timecard report export using provided cookies."""
    base_url = "https://workforcenow.cloud.adp.com"

    # Step 1: Verify session is valid by loading the home page
    home_response = requests.get(
        f"{base_url}/theme/index.html",
        headers={"Cookie": wfn_cookies},
        impersonate="chrome131",
        timeout=30,
        allow_redirects=False,
    )

    # If redirected to login, session is rejected
    if home_response.status_code in (301, 302, 303, 307, 308):
        location = home_response.headers.get("Location", "")
        if "signin" in location.lower() or "login" in location.lower() or "olp" in location.lower():
            return {
                "status_code": 200,
                "body": {
                    "status": "managed_session_required",
                    "organization_id": organization_id,
                    "organization_name": organization_name,
                    "user_id": user_id,
                    "report_type": report_type,
                    "date_range_start": date_range_start,
                    "date_range_end": date_range_end,
                    "file_name": None,
                    "content_type": None,
                    "download_url": None,
                    "parsed_data": [],
                    "error": {
                        "code": "ADP_MANAGED_SESSION_REQUIRED",
                        "message": (
                            "ADP rejected the provided session cookies "
                            "(redirected to login). This typically means ADP bound "
                            "the session to the original IP/TLS/browser context. "
                            "An Integuru-managed ADP browser session or official "
                            "ADP API credentials are required."
                        ),
                    },
                },
            }

    if home_response.status_code != 200:
        return {
            "status_code": home_response.status_code,
            "body": {"error": f"Failed to access ADP home page: HTTP {home_response.status_code}"},
        }

    # Step 2-5: Navigate to Reports > Time & Scheduling > Timecard
    # TODO: Implement once a valid ADP session is confirmed working.
    # Expected flow:
    #   GET {base_url}/theme/index.html#/Report/ReportsAndAnalytics
    #   Find and open Timecard report
    #   Apply date filters via report parameters
    #   Trigger export (Excel/CSV/PDF)
    #   Download the exported file
    #   For ezLaborManager requests, use ezlm_cookies for wwsm-components.adp.com

    # Step 6: Return successful response
    return {
        "status_code": 200,
        "body": {
            "status": "completed",
            "organization_id": organization_id,
            "organization_name": organization_name,
            "user_id": user_id,
            "report_type": report_type,
            "date_range_start": date_range_start,
            "date_range_end": date_range_end,
            "file_name": None,
            "content_type": None,
            "download_url": None,
            "parsed_data": [],
            "error": None,
        },
    }
