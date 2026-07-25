curl -i -H "Authorization: Bearer devtoken" ^
     -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" ^
     -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl-test\",\"version\":\"0\"}}}" ^
     http://mail-mcp.fynnluft.com/mcp
