# Included inside the proxy location. Writes are NEVER replayed to another slot.
proxy_pass http://comics_api;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $comics_connection_upgrade;
proxy_next_upstream off;
proxy_request_buffering off;
proxy_buffering off;
proxy_connect_timeout 10s;
proxy_read_timeout 180s;
proxy_send_timeout 180s;
