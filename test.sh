
curl -d '{"api_key" : "6823d73a-0b79-4189-a03c-55304b28c72e", "publish_webhook" : "http://www.google.com/1", "publish_end_webhook" : "http://www.google.com/2"}' -H 'Content-Type: application/json' http://localhost:4242/getStreamKey/

curl -d "name=BSWYX1GDHSV4MLJF6HDECZ1U9" -X POST  http://localhost:4242/publish/

curl -d "name=BSWYX1GDHSV4MLJF6HDECZ1U9" -X POST  http://localhost:4242/resetToken/

