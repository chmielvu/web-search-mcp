# GitHub YouTube Batch Transcription and Analysis Repositories

## Key Repositories

1. **virtonen/youtube-channel-transcript-downloader**
   - **Focus**: Channel-level transcript retrieval with title headers.
   - **Key Module**: `get_all_video_ids(channel_id)` — enumerates all videos using `youtube.search().list()` (100 units/page).
   - **Integration Point**: Incorporate this logic into `src/kindly_web_search_mcp_server/youtube/playlist_api.py` to provide quota-efficient channel listing.

2. **rugabunda/Youtube-Channel-Transcription-Downloader**
   - **Focus**: Robust rate-limiting, retry logic, and parallel workers.
   - **Key Module**: `Youtube.Transcribe.py` — implements delay/jitter, exponential backoff, and `concurrent.futures.ThreadPoolExecutor` for parallel downloads.
   - **Integration Point**: Apply these patterns to a new `src/kindly_web_search_mcp_server/youtube/batch_transcriber.py` for handling multi-video jobs.

3. **tainguyen07/whisper-subtitle**
   - **Focus**: High-performance ASR pipeline with Silero VAD and faster-whisper.
   - **Key Module**: `Orchestrator` class — manages audio decoding, VAD chunking, window batching, and subtitle rendering.
   - **Integration Point**: Adopt the `Orchestrator` architecture to enhance `src/kindly_web_search_mcp_server/youtube/vps_whisper.py` and `cf_whisper.py`.

4. **danielcliu/youtube-channel-transcript-api**
   - **Focus**: Specialized API for channel/playlist transcript retrieval.
   - **Key Module**: `YoutubeChannelTranscripts` class — handles channel search and transcript fetching with language fallback.
   - **Integration Point**: Use as a model for the proposed `youtube_channel_transcript` tool signature.

5. **genekogan/youtube-summarizer**
   - **Focus**: End-to-end audio download and LLM summarization.
   - **Key Module**: `summarize_youtube(url, num_bullet_points)` — combines `yt-dlp` download, FFmpeg processing, and OpenAI Whisper/GPT-4.
   - **Integration Point**: Inform the development of a `youtube_summarizer` tool.

## Synthesis & Recommendations

- **Channel Enumeration**: Use `youtube.search().list()` for broad discovery, but prefer `playlistItems.list` (Uploads Playlist) for 100x quota savings.
- **Rate Limiting**: Implement a global semaphore with random jitter (±20%) and 1.5s minimum delay between requests to avoid IP bans.
- **Parallel Processing**: Use `asyncio` or `concurrent.futures` with a configurable worker count (default 3, max 10) for batch operations.
- **ASR Pipeline**: Integrate Silero VAD pre-segmentation to reduce hallucinations and window batching for concurrent Whisper inference.

## Next Steps

1. Implement `src/kindly_web_search_mcp_server/youtube/playlist_api.py` for efficient channel/playlist enumeration.
2. Create `src/kindly_web_search_mcp_server/youtube/batch_transcriber.py` with rate-limiting and parallel execution.
3. Update `cascade.py` and `vps_whisper.py` with VAD and window batching logic.
4. Develop the `youtube_channel_transcript` tool in `tools/youtube.py`.
