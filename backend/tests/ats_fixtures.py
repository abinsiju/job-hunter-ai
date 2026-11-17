"""Local replicas of ATS application forms, in the label styles each platform uses, plus hostile pages. The server records every form
submission and every CAPTCHA click, so tests can prove the assistant never causes either."""
import http.server
import threading
from urllib.parse import urlparse

YN = lambda name, legend: (f'<fieldset><legend>{legend}</legend><label><input type="radio" name="{name}" value="yes"> Yes</label> '
                           f'<label><input type="radio" name="{name}" value="no"> No</label></fieldset>')
SUBMIT = '<button type="submit">Submit application</button>'


def page(body: str, head: str = "") -> str:
    return f"<!doctype html><html><head><meta charset=utf-8>{head}</head><body>{body}</body></html>"


WORKABLE = page(f"""<h1>Service Desk Analyst</h1><form id="f" action="/submit" method="post">
<label for="firstname">First name *</label><input id="firstname" name="firstname" required>
<label for="lastname">Last name *</label><input id="lastname" name="lastname" required>
<label for="email">Email *</label><input id="email" name="email" type="email" required>
<label for="phone">Phone</label><input id="phone" name="phone" type="tel">
<label for="resume">Resume *</label><input id="resume" name="resume" type="file" required>
<label for="cl">Cover letter</label><textarea id="cl" name="cl"></textarea>
<label for="li">LinkedIn profile *</label><input id="li" name="li" required>
<label for="sal">Expected salary</label><input id="sal" name="sal">
{YN("wr", "Do you have unrestricted work rights in Australia? *")}
<label for="cit">Are you an Australian citizen or permanent resident? *</label>
<select id="cit" name="cit" required><option value="">Select...</option><option>Yes</option><option>No</option></select>
{YN("lic", "Do you hold a current driver's licence?")}{YN("stu", "Are you currently studying?")}
<label for="hear">How did you hear about us?</label><input id="hear" name="hear">
<label><input type="checkbox" id="consent" name="consent" required> I agree to the privacy policy</label>{SUBMIT}</form>""")

GREENHOUSE = page(f"""<h1>Application</h1><form action="/submit" method="post">
<div class="field"><label>First Name <input id="first_name" name="job_application[first_name]" type="text"></label></div>
<div class="field"><label>Last Name <input id="last_name" name="job_application[last_name]" type="text"></label></div>
<div class="field"><label>Email <input id="email" name="job_application[email]" type="text"></label></div>
<div class="field"><label>Phone <input id="phone" name="job_application[phone]" type="text"></label></div>
<div class="field"><label>Location (City) <input id="location" type="text"></label></div>
<div class="field"><label for="resume">Resume/CV</label> <input type="file" id="resume"></div>
<div class="field"><label for="cover">Cover Letter</label> <input type="file" id="cover"></div>
<div class="field"><label for="state">State</label><select id="state"><option value="">Select</option><option>New South Wales</option><option>Victoria</option></select></div>
<div class="field"><label for="ref">Referee name</label><input id="ref"></div>{SUBMIT}</form>""")

LEVER = page(f"""<h1>Apply</h1><form action="/submit" method="post">
<input name="name" aria-label="Full name"><input name="email" aria-label="Email"><input name="phone" aria-label="Phone">
<input name="location" aria-label="Current location"><input type="file" name="resume" aria-label="Resume/CV">
<input name="urls[LinkedIn]" aria-label="LinkedIn URL">
<textarea name="comments" aria-label="Add a cover letter or anything else you want to share"></textarea>{SUBMIT}</form>""")

SMARTRECRUITERS = page(f"""<h1>Apply</h1><form action="/submit" method="post"><x-field label="First name"></x-field><x-field label="Last name"></x-field>
<x-field label="Email"></x-field><input type="tel" placeholder="Phone number" id="ph"><input type="file" id="only-file">{SUBMIT}</form>""",
                       head="""<script>customElements.define('x-field', class extends HTMLElement { connectedCallback(){
 const r=this.shadowRoot||this.attachShadow({mode:'open'}); r.innerHTML='<label>'+this.getAttribute('label')+' <input type="text"></label>'; } });</script>""")

CAPTCHA = page(f"""<form action="/submit" method="post"><label for="a">First name</label><input id="a"><label for="b">Last name</label><input id="b">
<label for="c">Email</label><input id="c" type="email"><div class="g-recaptcha" data-sitekey="x" onclick="fetch('/captcha-click')" style="width:300px;height:80px;border:1px solid #888">I'm not a robot</div>{SUBMIT}</form>""")
LOGIN = page('<h1>Sign in to apply</h1><form action="/login" method="post"><label for="u">Email</label><input id="u"><label for="p">Password</label><input id="p" type="password"><button>Sign in</button></form>')
NOFORM = page("<h1>Careers</h1><p>No openings are accepting applications right now.</p>")
JOB = page('<h1>Service Desk Analyst</h1><p>Join our team.</p><a href="/workable/apply" id="open">Apply for this job</a>')
ODD = page(f"""<form action="/submit" method="post"><label for="a">First name</label><input id="a"><label for="b">Last name</label><input id="b">
<label for="c">Email</label><input id="c" type="email">
<label for="wr">Your unrestricted work rights status</label><select id="wr"><option value="">Select</option><option>Citizen</option><option>Permanent resident</option><option>Visa holder</option></select>
<label for="f2">Free text about unrestricted work rights</label><input id="f2">{SUBMIT}</form>""")
TRAP = page('<form action="/submit" method="post"><button type="submit" id="sub">Submit application</button></form>'
            '<form><button id="inform">Apply</button></form><button id="send">Send application</button>'
            '<button id="ok" onclick="fetch(\'/opener-click\')">Apply now</button>')

CITIZEN_ONLY = page(f"""<h1>Graduate Analyst</h1><p>Applicants must be Australian citizens. Baseline security clearance required.</p><form action="/submit" method="post">
<label for="firstname">First name *</label><input id="firstname" name="firstname"><label for="lastname">Last name *</label><input id="lastname" name="lastname">
<label for="email">Email *</label><input id="email" name="email" type="email"><label for="resume">Resume *</label><input id="resume" type="file">{SUBMIT}</form>""")

ROUTES = {"/citizen/apply": CITIZEN_ONLY, "/workable/apply": WORKABLE, "/greenhouse/apply": GREENHOUSE, "/lever/apply": LEVER, "/smartrecruiters/apply": SMARTRECRUITERS, "/captcha/apply": CAPTCHA,
          "/login/apply": LOGIN, "/noform": NOFORM, "/workable/job": JOB, "/odd": ODD, "/trap": TRAP}


class ATSServer:
    def __init__(self):
        outer = self
        self.hits: list[tuple[str, str]] = []  # (method, path) of every submission-like request

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body="", headers=()):
                data = body.encode()
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlparse(self.path).path
                if path in ("/captcha-click", "/opener-click", "/submit"):
                    outer.hits.append(("GET", path))
                    return self._send(200, "ok")
                if path == "/redirect":
                    return self._send(302, "", [("Location", f"http://localhost:{outer.port}/workable/apply")])
                if path in ROUTES:
                    return self._send(200, ROUTES[path])
                self._send(404, "not found")

            def do_POST(self):
                outer.hits.append(("POST", urlparse(self.path).path))
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._send(200, "<h1>Application received</h1>")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def submissions(self):
        return [h for h in self.hits if h[1] == "/submit"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
