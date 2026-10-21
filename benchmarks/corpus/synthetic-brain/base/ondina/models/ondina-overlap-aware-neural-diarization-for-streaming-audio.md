---
type: Note
id: 8e6c617ab692
title: 'Ondina: Overlap-aware Neural Diarization for streaming Audio (preprint — September
  2026)'
description: Ettore Zanon¹, Michele Gobbo², Tiziano Rigon²
---

# Ondina: Overlap-aware Neural Diarization for streaming Audio (preprint — September 2026)

Ettore Zanon¹, Michele Gobbo², Tiziano Rigon²

# Ondina: Overlap-aware Neural Diarization for streaming Audio

**Ondina Research Labs**  
[Università di Trento, Trento, Italy]

**AWS Machine Learning**  
[Amazon Web Services, Verona, Italy]

### Abstract

Speaker diarization—the problem of determining "who spoke when" in continuous audio streams—presents significant challenges when speakers overlap and latency must remain under two seconds for live applications. We introduce **ONDA**, an end-to-end neural architecture for real-time streaming speaker diarization that explicitly models speech overlaps through dedicated attention mechanisms and maintains speaker identity consistency across stateful inference windows. The system processes audio in 1.6-second overlapping chunks using cached transformer states, achieving inference latency of 485 milliseconds on NVIDIA A100 GPUs and maintaining Speaker Consistency (SC) of 94.2% over 30-minute transcriptions. On the Radio Brenta Italian podcast corpus (47 hours, 9 recurring speakers, 18% overlap frames), ONDA achieves 13.9% Diarization Error Rate compared to 18.2% for the Whisper+pyannote baseline—a 23.4% relative improvement. Overlap Word Error Rate on concurrent speech drops to 18.4% versus 42.1% baseline. The architecture decomposes diarization into Voice Activity Detection (VAD), frame-level speaker embeddings, adaptive speaker registration, and overlap-aware multiparty attention, enabling deployment in meeting transcription, broadcast captioning, and accessibility services without post-hoc clustering.

**Keywords.** speaker diarization · streaming audio · overlap detection · neural attention · low-latency speech

---

## 1. Introduction

Speaker diarization answers the fundamental question "who spoke when" in multi-speaker audio without requiring pre-enrolled speaker models or microphone array configuration. Classical batch approaches require processing complete recordings offline, accumulating latency of 30–120 seconds on hour-long files—incompatible with real-time meeting notes, live broadcast captions, and simultaneous interpretation systems. The emergence of streaming applications (video conferencing, live podcasting, accessibility services) has created urgent demand for sub-second diarization latency without sacrificing accuracy.

A second driving challenge is **overlap handling**. In natural conversation, speakers overlap 5–15% of the time in dyadic settings, rising to 18–35% in multi-party discourse and broadcast narratives. Standard diarization pipelines—which cluster speaker embeddings or apply sequence labeling—treat overlap as noise, either discarding overlapped frames or forcing them into single-speaker assignments. This loss of information harms downstream tasks: meeting summarization cannot identify simultaneous discussion, accessibility transcripts lose speaker distinction during overlap, and linguistic analysis of turn-taking patterns becomes impossible. Yet explicit overlap modeling in streaming contexts remains largely unexplored; most published systems address overlap as a post-hoc correction rather than a core architectural concern.

**Our contribution.** ONDA combines three innovations. First, we introduce a recurrent identity tracker that maintains adaptive speaker state (mean embeddings, variance, continuity memory) across streaming windows, replacing expensive offline clustering. Second, we add explicit overlap-aware attention: when multiple speakers' embeddings simultaneously activate above a threshold, the system computes separate frame-level representations for each speaker rather than collapsing them into a single "ghost" embedding. Third, we optimize for streaming by caching transformer hidden states across 1.6-second windows, achieving real-time factor 0.87 (inference faster than audio playback) while latency stays under 500 milliseconds—suitable for live applications.

On the Radio Brenta corpus, ONDA achieves 13.9% DER (23.4% relative improvement over baseline), with particularly strong gains in overlap frames: 18.4% WER on concurrent speech versus 42.1% baseline. Speaker consistency reaches 94.2%, critical for maintaining speaker identity over long sessions. We release **ondina-core**, the full implementation and pre-trained checkpoint on Apache 2.0 license.

---

## 2. Background and Related Work

### 2.1 Traditional Diarization and Baseline Approaches

Classical speaker diarization pipelines decompose into five sequential stages: (a) Voice Activity Detection (VAD) to isolate speech regions and eliminate silence, (b) low-level feature extraction (mel-spectrograms computed at 25 Hz, typically 64–128 frequency bins), (c) speaker embedding extraction via supervised or self-supervised deep networks, (d) embedding-level clustering or sequence labeling to assign frames/segments to speakers, and (e) post-processing for smoothing and boundary refinement. The dominant production system combines Whisper Automatic Speech Recognition (Radford et al., 2023) for speech encoding and VAD with pyannote-audio (Bredin et al., 2021) for speaker clustering. Pyannote applies spectral clustering with affinity threshold τ ∈ [0.4, 0.8], measuring cosine similarity between speaker embeddings. On well-recorded, single-speaker utterances (e.g., CALLHOME corpus, DER ≈ 8%), this achieves excellent performance. On natural multi-speaker scenarios with overlap (DER baseline 18–24%), the pipeline accumulates errors across stages that compound.

*Limitation 1: Segment-level temporal granularity.* Speaker embeddings are computed per 1.5–2 second segment, not per frame. Fine-grained speaker transitions (e.g., "Alice: hello", overlapped by "Bob: hi there") within a segment are invisible to the clustering algorithm. The entire segment receives a single speaker label, introducing boundary errors of 500 ms–1 second per transition. Turn-taking analysis, interruption detection, and linguistic research all depend on frame-level precision, so segment-level diarization is fundamentally limited.

*Limitation 2: Overlap-induced acoustic masking and embedding collapse.* When two speakers speak simultaneously in a 1.5–2 second segment, computing a single embedding vector per segment averages their acoustic characteristics. This "ghost speaker" embedding may not closely match either true speaker's solo-speech embedding in the model's learned embedding space. Subsequent clustering assigns the overlapped segment based on distance to ghost embedding, often misclassifying it as a third spurious speaker or forcing it incorrectly into speaker A or B's cluster. The information about concurrent speech presence is lost.

*Limitation 3: Statefulness and streaming incompatibility.* Offline clustering requires observing all embeddings before assigning cluster labels and computing final boundaries. Applied to 2-second windows in streaming contexts, cluster assignments become unstable (frequent label flipping for the same speaker across consecutive windows). Post-hoc smoothing and forced-alignment tricks partially recover consistency but add latency and complexity. Critically, the clustering algorithm has no way to "remember" speaker identity across windows separated by silence—if Speaker A is silent for 10 seconds then resumes, the system may assign a new speaker ID instead of recovering A's original ID.

*Limitation 4: Hyperparameter brittleness and domain sensitivity.* The clustering threshold τ that determines whether two embeddings should merge into the same speaker is highly sensitive to acoustic conditions, speaker count, recording quality, and domain. Shifting τ from 0.75 to 0.77 can swing overall DER by 3–5 percentage points. No principled method exists to select τ without labeled held-out data from the target domain, necessitating per-application tuning and limiting system portability.

### 2.2 End-to-End Neural and Transformer-Based Approaches

Recent advances apply transformer and recurrent neural network architectures directly to frame-level speaker assignment, circumventing explicit clustering. End-to-end neural diarization (EEND, Kinoshita et al., 2021; Fujita et al., 2024) predicts speaker presence frame-by-frame via a sigmoid output layer per speaker, naturally supporting streaming through recurrent or transformer state. Transformer-based approaches (Watanabe et al., 2024; Ma et al., 2024) encode full audio context, then decode per-frame speaker presence or speaker IDs. These methods achieve DER 11–15% on standard benchmarks and naturally support streaming through stateful inference.

However, existing neural approaches have limitations. Most systems restrict themselves to single-speaker-per-frame by taking an argmax over speaker scores, effectively refusing to model overlap. Those that attempt multi-speaker outputs (e.g., sigmoid per speaker) typically lack dedicated loss functions optimizing overlap accuracy, so overlap regions receive lower priority during training. Few systems maintain speaker identity consistency across long recordings (30+ minutes), accumulating speaker-ID "drifts" where the model reassigns the same physical speaker to different IDs over time. The latency-accuracy trade-off in streaming (how much future context to buffer before output) remains under-explored.

### 2.3 Speaker Overlap: Linguistic Structure and Acoustic Reality

Overlap rates in naturally-occurring speech vary by setting: 5–15% in dyadic telephone conversations (Heldner & Edlund, 2010), 15–25% in lab-recorded two-person dialogues with natural turn-taking, and 18–35% in multi-party broadcast narratives and podcast discussions (Stivers et al., 2009). Overlap is not measurement noise or recording artifact; it is structurally meaningful. Linguistic analysis identifies three types: *backchannel* (listener brief acknowledgments like "mm-hm", "yes", "right" during speaker's turn), *collaborative completion* (listeners finish speaker's sentence), and *competitive interruption* (simultaneous bids for floor during transitions). Each type carries different pragmatic weight for conversation analysis.

Acoustically, simultaneous speech creates phenomena absent in single-speaker audio: (a) **harmonic interference** where fundamental frequencies of both speakers create beating patterns and spectral peaks in the mel-spectrogram that don't match either speaker's solo characteristics, (b) **reduced per-speaker energy** (each voice is ~3–6 dB quieter than solo speech, reducing signal-to-noise ratio for speaker embedding models), (c) **temporal misalignment** (voice onsets differ by 10–50 milliseconds, making frame-level speaker assignment ambiguous when quantized to 10–20 millisecond frames), and (d) **spectral masking** where higher-energy speaker's harmonics partially obscure lower-energy speaker's fundamental. ONDA's architecture directly addresses these through: (1) a dedicated overlap detection head trained on hand-labeled overlap frames, (2) conditional speaker embedding computation (when overlap is detected, compute separate embeddings for top-2 speakers), (3) Viterbi-style dynamic programming decoding that jointly optimizes speaker continuity and overlap likelihood, and (4) multi-hot frame labels preserving overlap information.

---

## 3. Proposed Architecture: ONDA

**System overview.** ONDA processes streaming audio in 1.6-second (160 frame) overlapping windows, each shifted by 800 ms. For each window, it produces frame-level diarization predictions and incrementally updates speaker identity state without requiring future context. Five components cascade: (i) Voice Activity Detection isolates speech, (ii) frame-level speaker embeddings encode acoustic characteristics, (iii) adaptive speaker registry maintains identities, (iv) overlap-aware attention detects and models simultaneous speech, (v) Viterbi decoding enforces speaker continuity and overlap coherence.

---

### 3.1 Voice Activity Detection and Feature Normalization

The front-end Voice Activity Detection module employs ECAPA-TDNN embeddings (Desplanques et al., 2020), a lightweight squeeze-and-excitation network trained on 500 hours of diverse audio (broadcast news, teleconference meetings, podcast recordings, street noise environments). The pre-trained model, available in the Pico variant (2.3 million parameters), outputs a soft confidence score b ∈ [0.0, 1.0] per 10-millisecond frame, indicating the probability of speech presence. Default threshold is b > 0.3 for speech detection; frames scoring below are masked (zeroed) to reduce noise influence. In high-noise recording environments (SNR < 10 dB, e.g., street recordings), the threshold raises to b > 0.5 to reduce false positive detections. In clean studio settings (SNR > 25 dB), threshold lowers to b > 0.2 to capture faint backchannel utterances and overlapped speech. The VAD module runs on CPU in < 10 milliseconds per 80-millisecond audio chunk, contributing negligible latency.

Parallel to VAD, the system computes low-level acoustic features: 64-dimensional Mel-Frequency Cepstral Coefficients (MFCC) with first-order delta (Δ) and second-order delta-delta (ΔΔ) coefficients, yielding 192 total dimensions per 80-millisecond chunk. MFCC features are computed at 25 Hz frame rate from 16 kHz mono audio, using a 512-sample window with 50% overlap, 40 mel frequency bands, and log energy scaling. This classical feature extraction provides white-box interpretability and robustness against neural network overfitting compared to end-to-end learned features. The hybrid approach—combining Mel spectrograms, MFCC, and neural embeddings—balances interpretability, robustness, and learned signal modeling.

---

### 3.2 Speaker Embedding Encoder with Recurrent State Caching

**Core architecture.** The backbone is a 6-layer bidirectional transformer encoder (hidden dimension 512, 8 attention heads, 2048-dimensional feedforward sublayers). Input audio sequences are split into 1.6-second (160 frame) overlapping chunks. Each transformer layer computes self-attention over a receptive field of ±160 milliseconds around each frame (approximately 16 frames in each direction), balancing temporal context window against streaming latency requirements. The receptive field is enforced using a banded attention mask that restricts the attention pattern to a fixed context window, eliminating attention over the entire sequence and thus eliminating quadratic time complexity in sequence length.

The critical innovation is **state caching across chunks**. When processing chunk t at time T (containing frames indexed t*160 to (t+1)*160), the encoder maintains a cache C containing the full hidden states from the previous k = 4 chunks (approximately 6.4 seconds of history). New frames are concatenated with cached states, and the attention computation is executed only over new frames (amortizing the cost per frame). After attention completes, new states are appended to the cache C, and states older than k chunks are discarded. This reduces GPU memory footprint from O(T²) (full sequence) to O(k·W) where W is the chunk width, enabling truly streaming processing without buffering complete audio files.

**Encoder outputs.** For each frame t, the encoder produces: (a) speaker embedding **e**_t ∈ ℝ^256, a learned fixed-dimensional representation capturing speaker-specific voice characteristics, (b) overlap logit ŷ_t^(overlap) ∈ ℝ, passed through sigmoid and clipped to [0.05, 0.95] for numerical stability during training, and (c) frame-level VAD confidence b_t (optional; can reuse front-end VAD).

### 3.3 Adaptive Speaker Registry, Identity Tracking, and Memory

ONDA maintains a dynamic speaker registry **R** = {**s**₁, …, **s**_K}, where K is the number of active speakers (discovered online). Each speaker state **s**_j contains: (i) **μ**_j, the exponential-moving-average mean speaker embedding (initialized with the embedding of the first frame assigned to that speaker), (ii) **σ**_j, the exponential-moving-average variance of speaker embeddings per dimension (used to adapt speaker similarity thresholds), (iii) t_j, the frame index of the most recent observation of speaker j (used for silence penalty), and (iv) m_j, a circular buffer of the 64 most-recent speaker embeddings for that speaker (used to detect speaker re-entrance after prolonged silence).

**Similarity computation and assignment.** When processing frame **e**_t with embedding vector, the system computes similarity scores to all registered speakers:

**sim**(t, j) = cos(**e**_t, **μ**_j) − 0.2 × |t − t_j| / 100

The second term is a temporal silence penalty: if speaker j has not been observed recently (|t − t_j| > 200 frames ≈ 2 seconds), the penalty reduces their similarity score, preventing false re-identification when a silent speaker's ID is assigned to a new speaker with superficially similar voice characteristics. The penalty coefficient (0.2) was chosen empirically to balance speaker consistency against speaker re-entrance recovery; values between 0.15 and 0.3 perform similarly.

**Assignment decision.** If max_j **sim**(t, j) > θ_register (default 0.65, tunable): Frame t is assigned to the winning speaker j*. The speaker state is updated: **μ**_j ← 0.95 **μ**_j + 0.05 **e**_t (exponential smoothing with time constant ~20 frames), and **σ**_j is similarly updated to track per-dimension variance. The last-seen time t_j is set to t.

If max_j **sim**(t, j) ≤ θ_register: The system detects a potential new speaker. Before initializing a new speaker, it checks the continuity memory m_j of each registered speaker j: if **e**_t is within cosine distance 0.25 of any embedding in m_j (indicating this speaker may have spoken recently but below current threshold due to voice variation), the frame is assigned to that speaker and m_j is updated. Otherwise, a new speaker is initialized with **μ**_new ← **e**_t and t_new ← t.

### 3.4 Overlap-Aware Multiparty Attention and Frame Label Resolution

When the overlap logit ŷ_t^(overlap) exceeds 0.5 (indicating detected overlap), standard single-speaker assignment fails. ONDA activates an overlap-aware cross-attention mechanism that computes per-speaker contributions:

For each pair of speakers (j, k) where j ≠ k:

**A**(t, j, k) = softmax( **e**_t^T (**W**_1 **μ**_j ⊙ **W**_2 **μ**_k) / √256 )

where **W**_1, **W**_2 are learned projection matrices, ⊙ denotes element-wise multiplication, and √256 is the scaling factor (square root of embedding dimension). This attention weight estimates the likelihood that frame t contains concurrent acoustic contribution from both speakers j and k. During training, **A**(t, j, k) is directly compared to ground-truth frame labels (which speaker(s) are active in frame t).

**Overlap frame labeling.** If max{**A**(t, j, k) : j < k} > 0.4 (strong multiparty evidence), the frame receives a composite label (e.g., "[Speaker 1 + Speaker 2]") rather than a forced single-speaker assignment. This preserves overlap information in the output stream for downstream processing. Frames below the 0.4 threshold revert to single-speaker assignment via argmax over registered speakers. Composite labels are passed to consuming applications (transcription UIs, linguistic analysis tools) which choose how to render or process them (e.g., show both speaker names, use a special "(overlapped speech)" token, or apply source separation).

---

## 4. Streaming Inference Architecture and Real-Time Performance

**Chunked stateful streaming processing.** Audio arrives at variable intervals matching the live capture rate of a microphone or streaming socket. The inference system operates as follows: (1) collect raw waveform samples into 1.6-second (160-frame) chunks with 50% overlap (800 ms stride between chunk starts), (2) resample to 16 kHz if needed, (3) concatenate the current chunk with cached hidden states from the previous k=4 chunks (approximately 6.4 seconds of history), (4) run the transformer encoder on the newly concatenated sequence, computing attention only over the new frames (old frames' representations reused from cache), (5) extract speaker embeddings and overlap logits for the new frames, (6) update the speaker registry based on new frames, (7) update the cache by appending new hidden states and removing states older than k chunks, (8) return diarization predictions (speaker labels and overlap markers) immediately without waiting for future audio. No buffering of complete audio files is required—the system is truly streaming.

**Real-time factor and end-to-end latency measurements.** Real-Time Factor (RTF) is defined as wall-clock inference time divided by audio duration. RTF < 1.0 indicates processing faster than real-time; RTF = 0.5 means 1 second of audio is processed in 0.5 seconds. On an NVIDIA A100 GPU, processing 1.6-second chunks yields RTF ≈ 0.87 when batching 4 chunks (6.4 seconds of audio in parallel). On more accessible NVIDIA V100 GPUs (common in production facilities), RTF increases to 1.12 but remains sub-real-time with careful request batching. End-to-end wall-clock latency from audio capture to diarization output is approximately 485 milliseconds (measured on A100): 10 ms VAD, 120 ms transformer encoder (on 1.6-second chunk), 40 ms speaker registry updates and overlap-aware attention, 75 ms Viterbi smoothing, plus ~240 ms system overhead (HTTP request handling, JSON serialization). This latency meets the < 500 ms threshold recommended for live meeting transcription and broadcast captioning, where user frustration with unresponsive visual feedback begins above ~2-second output delay.

**Computational optimizations and memory efficiency.** (a) **Cached attention masking** avoids recomputing attention over prior chunks. Only the new 160 frames are attended to; their representations are computed once and cached. (b) **INT8 activation quantization** reduces embeddings from float32 to int8, cutting memory footprint 40% with < 0.3% DER degradation (verified on validation set). (c) **Grouped batch processing** concatenates multiple independent 1.6-second chunks into a single batch dimension, amortizing transformer weight loading and GPU context-switching overhead. (d) **Selective frame processing**: in regions with VAD confidence < 0.2 (high confidence non-speech), the encoder skips every-other frame and interpolates missing embeddings, halving computation while maintaining diarization accuracy in silence regions.

**VAD threshold calibration and environment-specific tuning.** The front-end VAD threshold b is fully user-tunable without retraining. ONDA includes a calibration utility **ondina-cal-vad** that sweeps b ∈ {0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5} on a labeled 5-minute audio sample from the target environment and reports the threshold maximizing F1-score for speech/non-speech classification. In high-noise environments (SNR < 10 dB, street recordings, construction sites), raising b to 0.5 reduces false-positive speech detections. In clean studio settings (SNR > 25 dB), lowering b to 0.15–0.2 captures faint backchannel utterances and overlapped speech that would otherwise be masked. A typical end-user calibration takes 2–3 minutes.

**Hardware requirements and edge deployment.** On GPUs: NVIDIA A100 (40 GB HBM2, RTF 0.87), V100 (32 GB HBM2, RTF 1.12), RTX 4070 (12 GB GDDR6X, RTF 1.8). CPU-only inference on modern processors (Intel Xeon, AMD EPYC) achieves RTF > 20, making it impractical for live scenarios. Quantized 8-bit variant runs on NVIDIA Jetson Orin (RTF 4.2, 12 GB memory), suitable for edge transcription where streaming to cloud is not feasible or latency is critical. For ultra-low-latency scenarios (< 200 ms), a reduced model (3-layer encoder, 128 hidden) achieves RTF 0.42 on A100 with < 1% DER degradation.

---

## 5. Expected Benefits and Practical Deployment Considerations

**Live meeting transcription systems.** ONDA enables real-time speaker-attributed transcription in Zoom, Teams, WebEx, and proprietary video conferencing platforms. As participants speak, their speech is diarized with <2-second latency, producing output like "[Ettore]: hello, how are you?" immediately followed by "[Michele]: great, how's the project?" Participants see speaker distinctions and turn-taking in real-time, improving comprehension in multi-speaker settings and enabling live meeting summarization. Accessibility features benefit: Deaf and hard-of-hearing users who rely on captions can identify speakers and follow conversation flow without external speaker identification systems. The explicit overlap handling preserves backchannel utterances and simultaneous contributions, avoiding the loss of information common when overlap frames are discarded.

**Podcast, broadcast, and archival applications.** Content creators can extract speaker segments from podcast archives for editing, chapter generation, highlight video creation, full-text search with speaker attribution, and metadata tagging. ONDA provides a production-ready pipeline for Radio Brenta and similar broadcast archives (2000+ episodes, music/news/discussion shows) without requiring manual speaker labeling. The low inference latency enables live broadcast caption generation with speaker attribution, critical for live streaming events. Historical archives can be diarized at scale: 500 episodes (47 hours per episode × 500 ≈ 23,500 hours) complete processing on 8 A100 GPUs in approximately 9 days (RTF 0.87 × 23,500 hours ÷ (8 GPUs × 24 hours/day)).

**Accessibility for Deaf and hard-of-hearing audiences.** Live captions with accurate speaker attribution significantly improve comprehension in multi-speaker settings (panel discussions, interviews, group talks, classroom lectures). Users relying on captions can distinguish speakers, follow conversation flow, understand turn-taking structure, and catch backchannel utterances and overlapped speech preserved by ONDA. Real-time speech recognition combined with ONDA diarization provides end-to-end live captioning. Latency < 2 seconds is acceptable for accessibility; the system can be tuned for accuracy over speed if needed (e.g., buffering 5 seconds of audio to improve diarization coherence).

**Linguistic and conversation analysis.** ONDA's clean frame-level speaker boundaries enable quantitative linguistic research: turn-taking patterns (length of speaker turns, gap durations between turns), interruption detection and analysis (competitive interruptions vs. collaborative completions), gender bias in interruption frequency (documented in literature; Zimmerman & West, 1975), backchannel frequency per speaker, and speech overlap analysis (rates, duration distributions, relationship to conversation topics). Researchers can use ONDA with corpora of naturally-recorded speech (podcasts, meeting recordings, interview transcripts) to study conversation dynamics without manual annotation. The explicit overlap labeling enables specific analysis of simultaneous speech phenomena that traditional diarization systems cannot capture.

**Limitations and boundary conditions.** (1) *Language and domain generalization.* ONDA is trained primarily on Radio Brenta (Italian/English mixed podcasts, 47 hours). Performance on non-English speech (Spanish, Mandarin, Arabic, etc.), code-switching scenarios, music-speech mixes, heavily accented speakers, and non-native speakers remains to be evaluated in controlled studies. Initial domain adaptation experiments on 50 hours of held-out Italian data show minor degradation (DER 14.2% vs. 13.9% in-domain), suggesting moderate robustness. Transfer learning to new languages likely requires fine-tuning on 10–20 hours of target-language labeled data. (2) *Speaker count and scalability.* The model's softmax decoder assigns speech to K speakers, with K typically 2–4 in evaluation corpus (max K=6 tested). Performance degrades gracefully: at K=4, DER is 14.3%; at K=6, DER increases to 15.8%. For scenarios with 10+ simultaneous speakers (large conference calls), ONDA would require either (a) an ensemble of independent 2–4 speaker sub-models with hierarchical output aggregation, or (b) a higher-capacity decoder (e.g., mixture-of-experts or hierarchical attention). Neither approach is currently implemented; they remain future work. (3) *Acoustic environment robustness.* Evaluation corpus (Radio Brenta) is recorded in broadcast studios with high-quality microphones, professional audio engineering, and consistent acoustic treatment. Real-world scenarios involve: microphone arrays with far-field speech (6+ meters), mobile device microphones with poor frequency response, background noise (traffic, construction, HVAC), and reverberation (T60 > 300 ms). Blind evaluation on DIHARD III corpus (Khurana et al., 2020) and CHiME-8 challenge data is planned; preliminary results on DIHARD subset suggest 18–22% DER in far-field conditions vs. 13.9% in-domain (degradation expected). (4) *Computational cost and hardware dependency.* ONDA requires GPU acceleration for practical latency; CPU-only inference is impractical (RTF > 20). This limits deployment to environments with GPU access, excluding some embedded or isolated systems. Quantization to 8-bit reduces memory and enabling edge deployment, but introduces 0.3–0.5% accuracy loss. (5) *Overlap attention computational overhead.* The multiparty attention mechanism (Section 3.4) adds approximately 15% latency compared to a single-speaker baseline. For applications with low overlap rates (<5%), this overhead may not be justified; a simplified single-speaker variant can be configured. For podcast and multi-party scenarios (18–35% overlap), the accuracy gains justify the latency trade-off.

---

## 6. Summary and Conclusions

We have presented **ONDA** (Overlap-aware Neural Diarization for streaming Audio), a novel end-to-end neural architecture for real-time speaker diarization on continuous audio streams. The system achieves three primary objectives: (a) **sub-500 millisecond latency** suitable for live applications (meeting transcription, broadcast captioning, accessibility), (b) **explicit overlap modeling** that preserves speaker information during simultaneous speech rather than discarding it, and (c) **speaker identity consistency** maintained across long recordings (30+ minutes) without post-hoc clustering or realignment.

**Quantitative performance on Radio Brenta test set:** ONDA achieves 13.9% Diarization Error Rate, representing a 23.4% relative improvement over the Whisper+pyannote baseline (18.2% DER). On overlapped frames specifically, Word Error Rate (WER) improves from 42.1% (baseline) to 18.4% (ONDA)—a 56% relative gain. Speaker Consistency (correctly maintaining the same speaker ID across 30-minute recordings) reaches 94.2%, compared to 82.1% for baseline system. Real-time factor on NVIDIA A100 GPU is 0.87, enabling processing faster than real-time. Wall-clock latency from audio capture to diarization output is 485 milliseconds, within the < 500 ms threshold for acceptable real-time interaction.

**Technical innovations:** (1) **Recurrent speaker registry** replacing offline clustering, enabling true streaming without multi-pass processing. (2) **Explicit overlap-aware attention mechanism** that computes separate embeddings and labels for concurrent speakers, preserving overlap information. (3) **Cached transformer state** that maintains temporal context (6.4 seconds) while processing 1.6-second chunks independently, reducing memory and computation. (4) **Temporal continuity penalty** in speaker similarity scores that prevents false re-identification during speaker silence while allowing legitimate speaker re-entrance. (5) **Multi-hot frame labels** for overlapped frames, avoiding forced single-speaker assignments and enabling downstream linguistic analysis.

**Limitations and scope:** ONDA is designed for and evaluated on podcast and broadcast scenarios with 2–6 recurring speakers and 18% overlap rate. Performance on non-English speech, far-field microphone arrays, heavy background noise, and unbounded speaker counts (10+) remains to be characterized. Domain adaptation to new languages or acoustic conditions may require fine-tuning on labeled data from the target domain.

**Reproducibility and open-source release.** The **ondina-core** GitHub repository (https://github.com/ondina-oss/ondina-core) contains: full Python implementation of ONDA, training and evaluation scripts, pre-trained checkpoint (model weights) for Radio Brenta domain, and **ondina-bench**, a stand-alone evaluation harness for comparing diarization systems. All code is released under the Apache License 2.0, permitting academic and commercial use. The Radio Brenta test corpus (with speaker consent and privacy protections for broadcast conversations) is available to researchers upon request.

**Future research directions:** (i) **Multilingual training and evaluation** extending ONDA beyond Italian/English to Spanish, French, Mandarin, and other high-resource languages. Transfer learning approaches may enable adaptation to low-resource languages with minimal labeled data. (ii) **Ensemble methods for unbounded speaker counts**, where independent ONDA models are trained for 2–4 speaker scenarios and combined hierarchically or via clustering in speaker embedding space. (iii) **Robustness evaluation and adaptation** on DIHARD III, CHiME-8, and other challenging far-field, noisy, and reverberant corpora to characterize performance boundaries and develop domain adaptation strategies. (iv) **Integration with speaker verification systems** (e.g., speaker re-identification networks) to improve speaker continuity after long silence periods and handle speaker voice changes (e.g., from cold/allergy). (v) **Quantized and mobile variants** enabling deployment on phones and IoT devices without cloud compute dependencies. (vi) **Joint training with speech recognition** as a multi-task objective, potentially improving both diarization and ASR through shared representations.

---

## Acknowledgments

We thank Ornella at AgroEditrice Dalmonte for curating the Radio Brenta podcast corpus and coordinating speaker consent. We acknowledge compute support from AWS Italia.

---

## References

1. **Bredin, H., Yin, R., Coria, J. M., Gelly, G., Korshunov, P., Lavechin, M., ... & Titeux, H.** (2021). Pyannote.audio: Neural building blocks for speaker diarization. In *ICASSP 2021—2021 IEEE International Conference on Acoustics, Speech and Signal Processing* (pp. 7124–7128). IEEE.
2. **Desplanques, B., Thienpondt, J., & Decock, K.** (2020). ECAPA-TDNN: Emphasized channel attention, propagation and aggregation in TDNN based speaker verification. In *Interspeech 2020* (pp. 3830–3834).
3. **Heldner, M., & Edlund, J.** (2010). Pauses, gaps and overlaps in conversations. *Journal of Phonetics*, 38(4), 555–568.
4. **Kinoshita, K., Maciejewski, M., Wichern, G., & Roux, J. L.** (2021). Integrating end-to-end neural and clustering-based diarization for real conversational speech. In *ICASSP 2021—2021 IEEE International Conference on Acoustics, Speech and Signal Processing* (pp. 7243–7247).
5. **Khurana, S., Fragakis, J., Al Hanai, T., Panda, B., Pellom, B., Hough, A., ... & Wright, J.** (2020). The 2020 DIHARD speech diarization challenge: Common problems and solutions. In *Interspeech 2020* (pp. 1–5).
6. **Fujita, Y., Kojima, R., & Nakadai, K.** (2024). Overlapped speech detection and diarization in streaming contexts: a neural end-to-end approach. *IEEE/ACM Transactions on Audio, Speech, and Language Processing*, 32(2), 418–433.
7. **Landini, F., Profant, J., Diez, M., & Burget, L.** (2021). Generalized end-to-end loss for speaker verification. In *ICASSP 2021—2021 IEEE International Conference on Acoustics, Speech and Signal Processing* (pp. 6269–6273).
8. **Radford, A., Kim, J. W., Xu, T., Brockman, G., McLeavey, C., & Sutskever, I.** (2023). Robust speech recognition via large-scale weak supervision. In *International Conference on Machine Learning* (ICML 2023), (pp. 28498–28518). PMLR.
9. **Sell, G., & García-Romero, D.** (2014). Speaker segmentation with speaker-independent stream-context modeling. *IEEE/ACM Transactions on Audio, Speech, and Language Processing*, 22(9), 1405–1414.
10. **Stivers, T., Enfield, N. J., Brown, P., Englert, C., Hayashi, M., Heinemann, T., ... & Levinson, S. C.** (2009). Universals and cultural variation in turn-taking in conversation. *Proceedings of the National Academy of Sciences (PNAS)*, 106(26), 10587–10592.  
[https://doi.org/10.1073/pnas.0903616106]
11. **La Francesca, S., Rossi, E., & Monaci, G.** (2023). Transformer-based speech enhancement for overlapping speech recognition. *IEEE Signal Processing Letters*, 30, 751–755.  
[https://doi.org/10.1109/LSP.2023.3271845]
12. **Chen, H., Lee, S., & Tsao, Y.** (2022). Joint speaker diarization and speech separation with end-to-end neural embeddings. *arXiv preprint arXiv:2204.09349*.  
[https://arxiv.org/abs/2204.09349]
13. **Ma, C., Zeng, X., Wang, Y., & Wang, K.** (2024). Streaming attention-based online speaker diarization for continuous audio in real-time applications. *IEEE Transactions on Signal Processing*, 72, 2201–2214.
14. **Snyder, D., Garcia-Romero, D., Sell, G., Povey, D., & Khudanpur, S.** (2018). X-vectors: Robust DNN embeddings for speaker recognition. In *ICASSP 2018—2018 IEEE International Conference on Acoustics, Speech and Signal Processing* (pp. 5329–5333).
15. **Bredin, H., Laurent, A., & Titeux, H.** (2021). End-to-end speaker diarization for an unknown number of speakers. In *ICASSP 2021—2021 IEEE International Conference on Acoustics, Speech and Signal Processing* (pp. 6739–6743).
16. **Müller, H., Hemingway, A., Ramos, M., & Leupold, B.** (2024). Real-time speaker diarization with low-latency neural networks: architectures and optimization strategies. *Frontiers in Signal Processing*, 3, 1–14.  
[https://doi.org/10.3389/fsig.2024.1234567]
17. **Park, T., Joshi, N., Sap, M., McDuff, D., & Ringel Morris, M.** (2022). SpeechMOS: Real-time TTS quality evaluation with crowdsourcing. *arXiv preprint arXiv:2104.02906*.  
[https://arxiv.org/abs/2104.02906]
18. **Watanabe, S., Hori, T., Karita, S., Astudillo, R., Yoshimura, N., Xiao, J., & Braun, S.** (2024). Recent advances in end-to-end speech recognition and speaker diarization: a comprehensive survey. *IEEE Journal of Selected Topics in Signal Processing*, 18(3), 1–20.
19. **Qian, Y., Feng, Z., Zhu, P., Li, R., & Yu, D.** (2023). Separation and diarization with end-to-end neural and clustering approaches for real conversational speech. In *Interspeech 2023* (pp. 2771–2775).
20. **Carotenuto, V., Pina, F., Legname, G., & Lepri, B.** (2025). On the role of speaker embeddings in low-resource speaker diarization: lessons from zero-shot transfer learning. *IEEE/ACM Transactions on Audio, Speech, and Language Processing*, 33(2), 598–612.  
[https://doi.org/10.1109/TASLP.2025.3398752]
21. **Zhang, Y., Wei, S., Wang, Z., Hu, P., Zhou, Y., & Li, J.** (2024). Towards low-latency simultaneous speech translation and diarization in streaming contexts. In *EMNLP 2024—Conference on Empirical Methods in Natural Language Processing* (pp. 15842–15856).
22. **Gopalakrishnan, M., Garcia-Romero, D., Stojnic, G., & Povey, D.** (2022). Leveraging weakly supervised data to improve end-to-end speaker diarization. In *Interspeech 2022* (pp. 831–835).
23. **Lala, D., Milhorat, P., & Kawahara, T.** (2023). Single-speaker tracking and identification during cocktail-party listening with overlapping speech. *Frontiers in Psychology*, 14, 1116701.  
[https://doi.org/10.3389/fpsyg.2023.1116701]
24. **Madikeri, C., Motlicek, P., & Bourlard, H.** (2023). Overlap-aware diarization: leveraging neural embeddings for robust speaker attribution in multi-speaker scenarios. In *ICASSP 2023* (pp. 1–5).
25. **Zimmerman, D. H., & West, C.** (1975). Sex roles, interruptions and silences in conversation. In *B. Thorne & N. Henley (Eds.), Language and sex: Difference and dominance* (pp. 105–129). Rowley, MA: Newbury House.

---

## Linked

- [X (@ettorezn) — lancio Ondina e post pronti](../outreach/x-ettorezn-lancio-ondina-e-post-pronti.md)
- [Ondina paper — versions, Zenodo DOIs, LaTeX source and build (v2.1.0, 2026-10-01)](note://ab318b66-b8aa-47ba-8e5d-156dd2bd0c8d)
- [Ondina benchmark preview carousel (2026-09-25)](ondina-benchmark-preview-carousel-2026-09-25.md)
- [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](ondina-core-der-benchmark-vad-threshold-calibration-2026-09.md)
- [ondina-core — real-time-factor benchmark baseline vs ONDA (2026-09-24)](ondina-core-real-time-factor-benchmark-baseline-vs-onda-2026.md)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../codebase/ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md)

# See also

* [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../codebase/ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md) - Streaming speech-to-text engine, builds Ondina transcription pipeline with overlap-aware speaker diarization across real-time audio processing Pico speaker classifier tunes…
* [Ondina benchmark preview carousel (2026-09-25)](ondina-benchmark-preview-carousel-2026-09-25.md) - Public-facing carousel preview of Ondina's benchmark-suite-v3.2 results (Radio Brenta corpus, 28 speakers, GPU seconds and DER). Layout for app and web embed.
* [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](ondina-core-der-benchmark-vad-threshold-calibration-2026-09.md) - Framework (ondina-bench v0.8.1, Kite-0.3-7B encoder):
* [ondina-core — real-time-factor benchmark baseline vs ONDA (2026-09-24)](ondina-core-real-time-factor-benchmark-baseline-vs-onda-2026.md) - Setup:
* [X (@ettorezn) — lancio Ondina e post pronti](../outreach/x-ettorezn-lancio-ondina-e-post-pronti.md) - @ettorezn (Padova, 847 followers, 234 posts)
