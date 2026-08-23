import { createServer } from "node:http";

const HEALTH_BODY = '{"status":"ok"}';
const HOST = process.env.HOST ?? "0.0.0.0";
const rawPort = process.env.PORT ?? "3000";

if (!/^\d+$/.test(rawPort)) {
  throw new Error("PORT must be an integer between 1 and 65535.");
}

const port = Number.parseInt(rawPort, 10);
if (port < 1 || port > 65535) {
  throw new Error("PORT must be an integer between 1 and 65535.");
}

const server = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/health/live") {
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-length": Buffer.byteLength(HEALTH_BODY),
      "content-type": "application/json",
    });
    response.end(HEALTH_BODY);
    return;
  }

  response.writeHead(404, { "content-length": "0" });
  response.end();
});

server.listen(port, HOST);
