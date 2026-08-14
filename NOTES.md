# Engineering notes — talkback latency & audio smoothness

Measured findings from perf work on **2026-08-13** (RTX 3070, 8GB, WSL2). Kept here
so we don't re-derive or re-break these. Numbers are from this box; re-measure if
the GPU/model changes.

## TL;DR — don't redo these
- **Keep `cudnn.benchmark` OFF** in `chatterbox_server.py`. It *hurt*. (§1)
- **TF32 is the real synth speedup**, not benchmark. Keep TF32 on. (§1)
- **Replies queue, they don't interrupt** — via `flock`, not process-killing. (§2)
- **The GPU is fine** — no rogue VRAM/CPU hog; ~5GB is the model itself. (§4)
- **qsay text preprocessing is ~0.04s** — negligible, don't optimize it. (§3)

## 1. Synthesis speed — `cudnn.benchmark` is a trap
The turbo model is **fp32**: `t3` (AR token decoder, 427M) + `s3gen` (vocoder,
266M) + `ve`. The **t3 autoregressive decode dominates** synth time and is
**memory-bandwidth-bound at batch=1** (reads all params per token, ~40 tok/s).

`torch.backends.cudnn.benchmark = True` (added in 4069b52 as "3-6x faster") was
actually a **pessimization**: the AR decode runs at a *new sequence length every
step*, so cuDNN re-autotunes kernels mid-generation → decode stalls from ~40 to
~6 tok/s. The genuine speedup in that commit was **TF32**.

Long-line realtime factor (RTF = synth_time / audio_duration), warm:
| setting | RTF | notes |
|---|---|---|
| `benchmark=True` (old) | ~1.1 | stally, min 0.68 — **slower than realtime** |
| `benchmark=False` (now) | ~0.6 | steady — **faster than realtime** |

Why RTF matters: streamed playback runs at **SPEED=1.2** (atempo), so it drains
audio 1.2× fast and needs **RTF < 0.83** to stay gapless clip-to-clip. Steady
state now measures 0.66–0.82 (incl. HTTP), so it clears the bar. The "plenty of
pauses" complaint was these synth stalls — **not** silence padding in clips
(checked: no −40dB lead/trail padding) and **not** the model being inherently slow.

Keep: `matmul.allow_tf32=True`, `cudnn.allow_tf32=True`. Leave `cudnn.benchmark`
OFF.

**Future headroom (untried, uncertain, more invasive — only if needed):**
- **bf16 weights on `t3`** — decode is memory-bound, so halving weight bytes could
  ~2× it. Hard-casting `TTS.t3.to(bfloat16)` alone fails (`mat1/mat2 dtype
  mismatch`) — the conditioning tensors must be cast too. Watch for NaNs.
- `torch.compile`, CUDA graphs for the decode loop.
- GPU sits at perf state **P2 / ~1725MHz** vs 2100MHz max under CUDA (normal for
  compute); forcing higher clocks is a marginal, fiddly lever on WSL.

## 2. Talkback concurrency — queue, don't interrupt
`.claude/speak-last-message.py` (the global Stop hook) speaks each reply.
- **Old behavior:** a new reply *killed* the one still speaking — a `.speak.pid`
  lockfile (`killpg`) plus a Windows-side SoundPlayer kill. The Windows kill via
  `Get-CimInstance` cost **~0.75s** of interop; a direct `taskkill /PID` is ~0.24s;
  a bare `powershell.exe -NoProfile` spawn floors at ~0.27s.
- **Now:** replies **queue**. The hook launches `flock -x scratch/talk.play.lock
  bash qsay.sh …`; a new reply waits for the current one to finish, then plays.
  Free lock → no wait, so the single-reply case is unchanged (~2.1s to first word).
  All the kill machinery was removed.
- **Tradeoff:** the lock spans synth+playback, so a *queued* reply doesn't
  synth-ahead during the previous one's playback (~1.5s beat between stacked
  replies). Fine, and only when replies pile up. If ever unwanted, move the lock to
  gate only playback (needs per-utterance clip prefixes to avoid scratch
  collisions).

## 3. Latency breakdown (hook → first word), for reference
`reply text known → poll transcript flush (~0.3–0.5s) → synth first clip (dominant)
→ first word`. qsay's pronounce/split preprocessing is **~0.04s** — do not bother
optimizing it. Synth is the whole game; see §1.

Per-`/say` call has a **~0.4s fixed floor** (HTTP + the s3gen 2-step mel/vocoder),
independent of text length — which is why very short comma-clauses have the worst
overhead ratio. Splitting into many tiny clips (qsay splits on `. ! ? ,`) trades a
faster first word for more gap opportunities; with RTF ~0.6 it stays gapless in
steady state anyway.

## 4. GPU / resource sanity
- RTX 3070, 8GB. Model resident ~5GB; ~2.9GB free. No second process competes for
  VRAM (Xwayland shows but is desktop). CPU load spikes during work were **our own
  benchmark churn** (curl+ffmpeg+powershell per trial), not a rogue process.
- `nvidia-smi` from WSL doesn't list the model's PID under compute-apps, but the
  memory usage is real.

## 5. Gotchas hit while working here
- **`pkill -f <pat>` matches the running shell's own command line.** If the pattern
  also appears in the command you're typing, pkill kills its own parent shell —
  symptom is a bare signal exit (143/144) with no output. Exclude `$$`/`$PPID`, or
  kill by captured PID / `killpg`.
- **Benchmarking the hook:** it derives `QSAY`/paths from its own file location
  (`HERE`/`PROJECT`), so a copy run from elsewhere resolves the wrong project and
  silently produces no audio. Run copies from inside `.claude/`.
- Verbatim-repeat speech is suppressed by the `.speak.last` anti-repeat hash; vary
  the text to force a re-voice.
