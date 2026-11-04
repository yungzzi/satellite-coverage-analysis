"""限制 JSON 请求体，包括没有 Content-Length 的分块请求。"""
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes=65_536):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > self.max_bytes:
                response = JSONResponse({'detail': {'code': 'body_too_large', 'message': 'JSON 请求体不能超过 64 KiB。'}}, status_code=413)
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get('more_body', False):
                break

        async def replay():
            return chunks.pop(0) if chunks else await receive()
        return await self.app(scope, replay, send)
