- **Updates after event errors:** Failed requests no longer discard valid
  buffered server updates. Updates wait for the remaining owned requests and
  are applied when the last one settles, including an error reply.
