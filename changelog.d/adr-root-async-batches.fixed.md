- **Background loading completion:** Root and deferred events now advertise owned background-work batches so loading
does not end at the first intermediate task update. WebSocket and SSE clients
release the originating loading state on the batch's explicit completion.
