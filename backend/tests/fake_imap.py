"""A tiny IMAP server standing in for Gmail. It speaks just enough IMAP for login, LIST, APPEND and LOGOUT, stores appended messages,
and records every command so tests can prove nothing else (and certainly no sending) ever happens."""
import socketserver
import threading
from email import message_from_bytes, policy
from email.message import EmailMessage


class FakeGmail:
    def __init__(self, user: str = "me@gmail.com", password: str = "app-password-1234"):
        self.user, self.password = user, password
        self.commands: list[str] = []
        self.drafts: list[tuple[str, str, EmailMessage]] = []  # (folder, flags, message)
        self.logins: list[str] = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, text: str):
                self.wfile.write(text.encode() + b"\r\n")
                self.wfile.flush()

            def handle(self):
                self.reply("* OK IMAP4rev1 fake gmail ready")
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", "replace").rstrip("\r\n")
                    tag, _, rest = text.partition(" ")
                    cmd = rest.split(" ", 1)[0].upper()
                    outer.commands.append(cmd)
                    if cmd == "CAPABILITY":
                        self.reply("* CAPABILITY IMAP4rev1"); self.reply(f"{tag} OK done")
                    elif cmd == "LOGIN":
                        parts = rest.split(" ", 2)
                        user, pw = (parts[1].strip('"'), parts[2].strip('"')) if len(parts) == 3 else ("", "")
                        outer.logins.append(user)
                        if user == outer.user and pw == outer.password:
                            self.reply(f"{tag} OK logged in")
                        else:
                            self.reply(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials")
                    elif cmd == "LIST":
                        self.reply('* LIST (\\HasNoChildren) "/" "INBOX"')
                        self.reply('* LIST (\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"')
                        self.reply('* LIST (\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"')
                        self.reply(f"{tag} OK listed")
                    elif cmd == "APPEND":
                        head, _, size = rest.rpartition("{")
                        folder = head.split('"')[1] if '"' in head else head.split()[1]
                        flags = head[head.find("(") + 1:head.find(")")] if "(" in head else ""
                        n = int(size.rstrip("}"))
                        self.reply("+ Ready for literal data")
                        data = self.rfile.read(n)
                        self.rfile.readline()  # the CRLF that ends the command
                        outer.drafts.append((folder, flags, message_from_bytes(data, policy=policy.default)))
                        self.reply(f"{tag} OK [APPENDUID 1 {len(outer.drafts)}] APPEND completed")
                    elif cmd == "LOGOUT":
                        self.reply("* BYE bye"); self.reply(f"{tag} OK logout"); return
                    else:
                        self.reply(f"{tag} BAD unsupported in fake server")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
