import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

class InfomentorClient:
    def __init__(self, base_url: str = "https://infomentor.se/swedish/production/mentor/"):
        self.base_url = base_url
        self.hub_url = "https://hub.infomentor.se"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7"
        })
        self.pupils: List[Dict[str, Any]] = []

    def login(self, username: str, password: str) -> bool:
        """Logs in via classic infomentor.se and bridges OAuth to hub.infomentor.se"""
        # Step 1: Fetch initial login page
        r1 = self.session.get(self.base_url)
        soup1 = BeautifulSoup(r1.text, "html.parser")
        form1 = soup1.find("form", id="mainForm")
        if not form1:
            raise RuntimeError("Could not find mainForm on Infomentor login page")

        data1 = {inp.get("name"): inp.get("value", "") for inp in form1.find_all("input") if inp.get("name")}
        data1["login_ascx$txtNotandanafn"] = username
        data1["login_ascx$txtLykilord"] = password
        data1["login_ascx$btnLogin"] = "Logga in"

        # Step 2: Submit credentials
        action1 = urljoin(r1.url, form1.get("action", "./"))
        r2 = self.session.post(action1, data=data1, allow_redirects=True)

        # Step 3: Handle OAuth bridge form to hub.infomentor.se
        soup2 = BeautifulSoup(r2.text, "html.parser")
        form2 = soup2.find("form", id="openid_message")
        if form2:
            action2 = urljoin(r2.url, form2.get("action"))
            data2 = {inp.get("name"): inp.get("value", "") for inp in form2.find_all("input") if inp.get("name")}
            r3 = self.session.post(action2, data=data2, allow_redirects=True)
            landing_html = r3.text
        else:
            landing_html = r2.text

        # Verify we are in Hub
        if "hub.infomentor.se" not in r2.url and "IMHome" not in self.session.cookies.get_dict():
            raise RuntimeError("Login failed: IMHome session cookie not found")

        # Parse pupils from landing HTML
        self._parse_pupils(landing_html)
        return True

    def _parse_pupils(self, html_content: str):
        """Extracts list of children and switch URLs"""
        self.pupils = []
        # Infomentor stores pupils in a JSON-like object inside the HTML
        match = re.search(r"\"pupils\"\s*:\s*(\[.*?\])\s*,\s*\"[a-zA-Z]+\"", html_content)
        if match:
            import json
            try:
                raw_pupils = json.loads(match.group(1))
                for p in raw_pupils:
                    self.pupils.append({
                        "name": p.get("name"),
                        "id": p.get("id"),
                        "switch_url": p.get("switchPupilUrl"),
                        "selected": p.get("selected", False)
                    })
            except Exception:
                pass

        # Fallback regex if full JSON parsing had edge cases
        if not self.pupils:
            matches = re.findall(r"switchPupilUrl\":\"([^\"]+SwitchPupil/(\d+))\".*?\"name\":\"([^\"]+)\"", html_content)
            for url, pid, name in matches:
                self.pupils.append({
                    "name": name,
                    "id": pid,
                    "switch_url": url,
                    "selected": False
                })

    def switch_pupil(self, switch_url: str) -> bool:
        """Switches active session to a specific child"""
        r = self.session.get(switch_url, allow_redirects=True)
        return r.status_code == 200

    def get_calendar(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Fetches calendar entries (exams, activities, holidays)"""
        url = f"{self.hub_url}/calendarv2/calendarv2/getentries"
        resp = self.session.post(url, json={"startDate": start_date, "endDate": end_date})
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
        return []

    def get_schedule(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Fetches timetable lessons from gettimetablelist"""
        url = f"{self.hub_url}/timetable/timetable/gettimetablelist"
        resp = self.session.post(url, json={"start": start_date, "end": end_date, "UTCOffset": -60})
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
        return []

    def get_uols(self) -> List[Dict[str, Any]]:
        """Fetches list of active subject planeringar (Units of Learning)"""
        url = f"{self.hub_url}/uolv2/uolv2/appData"
        resp = self.session.post(url, json={})
        if resp.status_code == 200:
            return resp.json().get("uols", [])
        return []

    def get_uol_details(self, uol_id: int) -> Optional[Dict[str, Any]]:
        """Fetches detailed study guide text for a specific planning"""
        url = f"{self.hub_url}/uolv2/uolv2/getuol"
        resp = self.session.post(url, json={"id": uol_id})
        if resp.status_code == 200:
            return resp.json()
        return None

    def get_messages(self) -> List[Dict[str, Any]]:
        """Fetches recent teacher messages and notifications"""
        url = f"{self.hub_url}/Message/Message/GetMessages"
        resp = self.session.post(url, json={})
        if resp.status_code == 200:
            return resp.json().get("items", [])
        return []

    def get_message_detail(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Fetches message body and metadata for a specific message"""
        url = f"{self.hub_url}/Message/Message/GetMessage"
        resp = self.session.post(url, json={"id": message_id, "messageId": message_id})
        if resp.status_code == 200:
            return resp.json()
        return None

    def get_news(self) -> List[Dict[str, Any]]:
        """Fetches weekly newsletters"""
        url = f"{self.hub_url}/Communication/News/GetNewsList"
        resp = self.session.post(url, json={})
        if resp.status_code == 200:
            return resp.json().get("items", [])
        return []
