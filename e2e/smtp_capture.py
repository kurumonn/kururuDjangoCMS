"""Small test-only SMTP sink with a read-only JSON summary endpoint."""

from __future__ import annotations

import asyncio
import json
from email import policy
from email.parser import BytesParser

MESSAGES: list[dict[str, object]] = []


async def _reply(writer: asyncio.StreamWriter, line: str) -> None:
    writer.write((line + "\r\n").encode("ascii"))
    await writer.drain()


async def handle_smtp(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    recipients: list[str] = []
    data_lines: list[bytes] = []
    in_data = False
    await _reply(writer, "220 smtp_capture ESMTP ready")
    try:
        while line := await reader.readline():
            if in_data:
                if line == b".\r\n":
                    raw = b"".join(data_lines)
                    message = BytesParser(policy=policy.default).parsebytes(raw)
                    MESSAGES.append(
                        {
                            "recipients": list(recipients),
                            "subject": str(message.get("Subject", "")),
                            "message_id": str(message.get("Message-ID", "")),
                        }
                    )
                    recipients.clear()
                    data_lines.clear()
                    in_data = False
                    await _reply(writer, "250 2.0.0 queued")
                    continue
                data_lines.append(line[1:] if line.startswith(b"..") else line)
                continue

            command = line.decode("utf-8", errors="replace").strip()
            verb, _, argument = command.partition(" ")
            verb = verb.upper()
            if verb in {"EHLO", "HELO"}:
                await _reply(writer, "250-smtp_capture")
                await _reply(writer, "250 SIZE 65536")
            elif verb == "MAIL":
                recipients.clear()
                await _reply(writer, "250 2.1.0 sender ok")
            elif verb == "RCPT":
                address = argument.split(":", 1)[-1].strip().strip("<>")
                recipients.append(address)
                await _reply(writer, "250 2.1.5 recipient ok")
            elif verb == "DATA":
                in_data = True
                data_lines.clear()
                await _reply(writer, "354 end with <CRLF>.<CRLF>")
            elif verb == "RSET":
                recipients.clear()
                data_lines.clear()
                in_data = False
                await _reply(writer, "250 2.0.0 reset")
            elif verb == "NOOP":
                await _reply(writer, "250 2.0.0 ok")
            elif verb == "QUIT":
                await _reply(writer, "221 2.0.0 bye")
                break
            else:
                await _reply(writer, "502 5.5.1 command not implemented")
    finally:
        writer.close()
        await writer.wait_closed()


async def handle_http(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    request_line = await reader.readline()
    while line := await reader.readline():
        if line in {b"\r\n", b"\n"}:
            break
    path = request_line.decode("ascii", errors="replace").split(" ")[1]
    if path == "/healthz":
        status = "200 OK"
        body = b'{"status":"ok"}'
    elif path == "/messages":
        status = "200 OK"
        body = json.dumps(MESSAGES, ensure_ascii=False).encode("utf-8")
    else:
        status = "404 Not Found"
        body = b'{"error":"not found"}'
    headers = (
        f"HTTP/1.1 {status}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    writer.write(headers + body)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main() -> None:
    # The container publishes no host ports. Binding its interfaces is required
    # so only the worker and Playwright peers on the Compose networks can reach it.
    smtp = await asyncio.start_server(
        handle_smtp, "0.0.0.0", 1025  # nosec B104
    )
    http = await asyncio.start_server(
        handle_http, "0.0.0.0", 8025  # nosec B104
    )
    async with smtp, http:
        await asyncio.gather(smtp.serve_forever(), http.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
