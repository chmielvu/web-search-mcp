# Query-centric search-event findings — current DuckDB Parquet export

**Status:** changed and verified. The live DuckDB database was exported in read-only mode with `EXPORT DATABASE ... (FORMAT parquet, COMPRESSION zstd)` and loaded with Polars 1.41.2. Primary unit: `1113` `search_runs` rows from `2026-07-16 15:34:49.823336+00:00` through `2026-09-15 19:49:01.308270+00:00` UTC. The report follows the seven requested focus areas; database-wide schema evidence is isolated to section 5.
- Query text is present and analyzable for `1113`/1113 runs. Median query length is `9.000` tokens / `65.000` characters; p95 is `16.000` tokens / `107.000` characters.
- Intent counts:

| intent | len |
| --- | --- |
| general | 680 |
| ai_coding_and_infrastructure | 368 |
| comparison | 36 |
|  | 14 |
| digital_humanities | 9 |
| news | 3 |
| social_media | 3 |

Aggregate quality rates are not representative because `general` and `ai_coding_and_infrastructure` dominate.
- The export records `2622` rewrite variants and `360` parseable rewrite-coverage judgments. Fidelity is reported as NLP overlap/length evidence, not unsupported semantic equivalence.
- Last-week latency is anchored to `2026-09-15 19:49:01.308270+00:00` and contains `122` runs. Cache-hit correlation is reported only when explicit instrumentation can be linked to a run; fingerprints are not treated as hits.

## Data and method

- DuckDB source: `duckdb_data/analytics/search_events.duckdb`.
- Parquet export: `duckdb_data\analytics\exports\2026-09-15\full_parquet`. Polars loaded every exported Parquet table for the schema audit; query-centric sections use only the event tables needed for the requested question.
- NLP preprocessing: Unicode word tokenization, lowercasing, explicit multilingual stopword removal for content signatures, dominant Unicode script classification, surface query-shape rules, token set precision/recall/F1/Jaccard, and character-sequence similarity. No claim of language identification or semantic embedding similarity is made.

## 1. Input query patterns

### Surface distributions

Query-shape counts:

| shape | len |
| --- | --- |
| keyword_long | 891 |
| keyword_short | 182 |
| question | 32 |
| imperative | 6 |
| url_like | 2 |

Dominant-script counts (script heuristic, not language detection):

| script | len |
| --- | --- |
| Latin | 1112 |
| CJK_or_EastAsian | 1 |

Stopword-profile counts (best lexical profile only; not a language detector):

| language_profile | len |
| --- | --- |
| none | 855 |
| pt | 129 |
| en | 119 |
| it | 9 |
| fr | 1 |

Length: median `9.000` tokens / `65.000` characters; p95 `16.000` tokens / `107.000` characters. Length uses `search_runs.query` and Unicode word tokens.

### Common tokens and stopwords

| token | count | stopword |
| --- | --- | --- |
| search | 248 | False |
| python | 235 | False |
| site | 182 | False |
| query | 137 | False |
| 2026 | 129 | False |
| api | 126 | False |
| github | 111 | False |
| com | 107 | False |
| code | 91 | False |
| docs | 87 | False |
| ai | 82 | False |
| web | 76 | False |
| mcp | 75 | False |
| graph | 70 | False |
| best | 68 | False |
| 2025 | 64 | False |
| 3 | 59 | False |
| agent | 57 | False |
| asyncio | 54 | False |
| tool | 53 | False |
| llm | 52 | False |
| fastmcp | 52 | False |
| official | 52 | False |
| documentation | 50 | False |
| library | 48 | False |
| retrieval | 47 | False |
| for | 46 | True |
| model | 46 | False |
| practices | 45 | False |
| production | 45 | False |

| stopword | count |
| --- | --- |
| for | 46 |
| to | 44 |
| a | 42 |
| or | 38 |
| and | 27 |
| how | 23 |
| in | 23 |
| what | 20 |
| of | 20 |
| the | 19 |
| with | 17 |
| from | 12 |
| are | 8 |
| do | 7 |
| by | 6 |

### Recency, repetition, and families

Observed time span is `2026-07-16 15:34:49.823336+00:00` to `2026-09-15 19:49:01.308270+00:00` UTC. Exact normalized repetition uses lowercase trimmed `normalized_query`: `62` keys repeat at least twice; the most repeated key occurs `30` times.

Largest repeated normalized queries:

| normalized_repeat_key | len |
| --- | --- |
| query a | 30 |
| python asyncio timeout handling | 13 |
| python 3.13 release highlights official documentation | 12 |
| python fastmcp | 12 |
| python 3.13 asyncio | 11 |
| most useful duckdb extensions | 9 |
| q | 8 |
| python asyncio tutorial | 7 |
| python asyncio documentation | 7 |
| python 3.14 release | 6 |
| fastmcp python framework 2026 | 6 |
| fastmcp sse vs streamable http transport differences | 6 |
| fastmcp server registration @mcp.tool decorator | 6 |
| test | 5 |
| python async patterns | 5 |
| production rag hybrid retrieval benchmark reranking | 4 |
| postgresql vacuum autovacuum tuning | 4 |
| how have eu ai act high-risk obligations for general-purpose ai models changed since the 2025 code of practice, and what do they mean for self-hosted rag products sold to eu banks? | 4 |
| what are the licensing, operational, and vector-search tradeoffs of replacing elasticsearch with opensearch for a multi-tenant logs platform that already uses kafka and clickhouse? | 4 |
| python 3.14 release date | 4 |

Query families use sorted unique content tokens after stopword removal. `63` multi-row families exist; the largest contains `30` rows. This intentionally conservative family definition catches reorderings but not paraphrases.

Largest content-token query families:

| content_signature | len |
| --- | --- |
| query | 30 |
| asyncio handling python timeout | 13 |
| 13 3 asyncio python | 12 |
| 13 3 documentation highlights official python release | 12 |
| fastmcp python | 12 |
| asyncio python tutorial | 9 |
| duckdb extensions most useful | 9 |
| q | 8 |
| asyncio documentation python | 7 |
| 14 3 python release | 6 |
| decorator fastmcp mcp registration server tool | 6 |
| 2026 fastmcp framework python | 6 |
| differences fastmcp http sse streamable transport vs | 6 |
| test | 5 |
| python | 5 |
| async patterns python | 5 |
| 2025 act ai banks changed code eu general have high hosted mean models obligations practice products purpose rag risk self since sold they | 4 |
| 14 3 date python release | 4 |
| autovacuum postgresql tuning vacuum | 4 |
| benchmark hybrid production rag reranking retrieval | 4 |

Daily query drift (last 20 observed days):

| day | runs | mean_tokens | median_latency_ms | p95_latency_ms | rewrite_rate | success_rate |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-26 | 2 | 8.5 | 20730.117099996278 | 24964.14879999793 | 1.0 | 1.0 |
| 2026-08-27 | 3 | 12.333333333333334 | 18637.819299998228 | 27346.871600020677 | 1.0 | 1.0 |
| 2026-08-28 | 2 | 11.0 | 49565.0182999816 | 62980.47430001316 | 1.0 | 1.0 |
| 2026-08-29 | 14 | 12.142857142857142 | 65162.11005000514 | 67454.93370003533 | 0.7857142857142857 | 1.0 |
| 2026-08-30 | 38 | 10.763157894736842 | 42484.86105000484 | 67624.09820000175 | 0.8157894736842105 | 0.8157894736842105 |
| 2026-08-31 | 2 | 12.5 | 43812.0896499895 | 46903.452200000174 | 1.0 | 1.0 |
| 2026-09-02 | 22 | 10.727272727272727 | 24693.86625000334 | 43304.46769998525 | 0.8636363636363636 | 1.0 |
| 2026-09-03 | 38 | 7.2368421052631575 | 30918.787599992356 | 60737.692299997434 | 0.7631578947368421 | 1.0 |
| 2026-09-04 | 15 | 21.4 | 77449.63209994603 | 105248.31950000953 | 1.0 | 1.0 |
| 2026-09-05 | 47 | 7.829787234042553 | 42679.74449999974 | 70101.99509999802 | 0.6808510638297872 | 0.9361702127659575 |
| 2026-09-06 | 11 | 11.818181818181818 | 68696.3090999925 | 86031.44939999038 | 1.0 | 0.9090909090909091 |
| 2026-09-07 | 23 | 9.782608695652174 | 30672.7865000139 | 44862.63690001215 | 0.7391304347826086 | 0.9565217391304348 |
| 2026-09-08 | 28 | 9.714285714285714 | 24204.78140000114 | 38112.81969999982 | 0.8214285714285714 | 0.8928571428571429 |
| 2026-09-09 | 47 | 10.914893617021276 | 34883.64129999536 | 96078.97960000264 | 0.851063829787234 | 0.9574468085106383 |
| 2026-09-10 | 1 | 6.0 | 0.0 | 0.0 | 1.0 | 0.0 |
| 2026-09-11 | 6 | 11.666666666666666 | 21663.035499994294 | 65770.63949999865 | 1.0 | 0.6666666666666666 |
| 2026-09-12 | 43 | 10.44186046511628 | 19667.551000020467 | 39689.93059999775 | 0.6976744186046512 | 1.0 |
| 2026-09-13 | 3 | 11.0 | 34081.7332000006 | 44153.83770002518 | 1.0 | 1.0 |
| 2026-09-14 | 5 | 13.0 | 22719.665300042834 | 43323.77389998874 | 1.0 | 1.0 |
| 2026-09-15 | 17 | 9.470588235294118 | 35142.46679999633 | 43611.4396999983 | 1.0 | 1.0 |

## 2. Intent correctness

Observed judge alignment is based on `390` parseable `llm_judgments` rows with `judgment_kind='intent_coherence'`, successful status, and verdicts `coherent`, `partially_coherent`, or `incoherent`. The score is 1, 0.5, or 0 respectively.

| intent | judged_n | coherent_rate | partial_rate | incoherent_rate | coherence_score |
| --- | --- | --- | --- | --- | --- |
| social_media | 2 | 0.0 | 0.0 | 1.0 | 0.0 |
| digital_humanities | 4 | 0.0 | 0.0 | 1.0 | 0.0 |
|  | 6 | 0.0 | 0.0 | 1.0 | 0.0 |
| general | 233 | 0.09012875536480687 | 0.24034334763948498 | 0.6695278969957081 | 0.21030042918454936 |
| ai_coding_and_infrastructure | 123 | 0.23577235772357724 | 0.4146341463414634 | 0.34959349593495936 | 0.44308943089430897 |
| news | 1 | 0.0 | 1.0 | 0.0 | 0.5 |
| comparison | 21 | 0.42857142857142855 | 0.47619047619047616 | 0.09523809523809523 | 0.6666666666666666 |

Priority classes with at least 20 judged queries and coherence score below 0.5: `[{'intent': 'general', 'judged_n': 233, 'coherent_rate': 0.09012875536480687, 'partial_rate': 0.24034334763948498, 'incoherent_rate': 0.6695278969957081, 'coherence_score': 0.21030042918454936}, {'intent': 'ai_coding_and_infrastructure', 'judged_n': 123, 'coherent_rate': 0.23577235772357724, 'partial_rate': 0.4146341463414634, 'incoherent_rate': 0.34959349593495936, 'coherence_score': 0.44308943089430897}]`. In this export, these are the classes to inspect first; sparse classes are intentionally not promoted from a small denominator.

Independent lexical expected-intent proxy cross-tab (review flag, not gold truth):

| intent | expected_intent_proxy | len |
| --- | --- | --- |
| general | ai_coding_and_infrastructure | 325 |
| general | general | 241 |
| ai_coding_and_infrastructure | ai_coding_and_infrastructure | 187 |
| ai_coding_and_infrastructure | news | 86 |
| general | news | 82 |
| ai_coding_and_infrastructure | general | 81 |
| comparison | comparison | 25 |
| general | comparison | 23 |
| ai_coding_and_infrastructure | comparison | 14 |
|  | general | 8 |
| general | digital_humanities | 7 |
|  | ai_coding_and_infrastructure | 6 |
| digital_humanities | general | 5 |
| comparison | general | 4 |
| comparison | news | 4 |
| digital_humanities | ai_coding_and_infrastructure | 3 |
| comparison | ai_coding_and_infrastructure | 3 |
| news | news | 2 |
| general | social_media | 2 |
| news | ai_coding_and_infrastructure | 1 |
| social_media | news | 1 |
| social_media | general | 1 |
| social_media | ai_coding_and_infrastructure | 1 |
| digital_humanities | news | 1 |

The proxy labels comparison/news/social-media/digital-humanities/coding-infrastructure cues before defaulting to general. Poor judge alignment or high proxy mismatch identifies classes for manual annotation; sparse classes are not reliable from rate alone. No independent human intent gold set is present in this export, so accuracy and Cohen's kappa are not claimed.

## 3. Query rewrite quality and decomposition

### Rewrite fidelity

Across `2622` `query_variants` rows: mean token precision `0.756`, recall `0.874`, F1 `0.785`, character similarity `0.743`, median rewrite/original length ratio `1.000`, p95 `3.333`. Over-rewrite is defined as rewrite token count `> 2 ×` original; under-rewrite is `< 0.5 ×` original **and** token recall `< 0.8`. Observed rates are `0.250` and `0.027`.

Variant-role fidelity and length:

| variant_role | n | mean_f1 | median_ratio | over_rate | under_rate |
| --- | --- | --- | --- | --- | --- |
| specialized | 28 | 1.0 | 1.0 | 0.0 | 0.0 |
| neural | 28 | 1.0 | 1.0 | 0.0 | 0.0 |
| original | 409 | 1.0 | 1.0 | 0.0 | 0.0 |
| original_free | 28 | 1.0 | 1.0 | 0.0 | 0.0 |
| free | 409 | 0.8203853799338213 | 1.1818181818181819 | 0.34474327628361856 | 0.009779951100244499 |
| semantic_tavily | 409 | 0.7463919084328262 | 1.0 | 0.12469437652811736 | 0.004889975550122249 |
| paid_google | 28 | 0.7448493203190234 | 2.4772727272727275 | 0.8214285714285714 | 0.0 |
| paid_other | 28 | 0.7448493203190234 | 2.4772727272727275 | 0.8214285714285714 | 0.0 |
| paid_brave | 28 | 0.737523312993016 | 2.4772727272727275 | 0.8214285714285714 | 0.0 |
| serp1 | 409 | 0.7349643120604767 | 1.0 | 0.33251833740831294 | 0.08068459657701711 |
| semantic_exa | 409 | 0.699354007555367 | 1.3636363636363635 | 0.3056234718826406 | 0.0024449877750611247 |
| serp2 | 409 | 0.6738334803900561 | 1.0 | 0.3276283618581907 | 0.07334963325183375 |

Query-transform rules observed in `query_transforms.rules_applied`:

| rules_applied | len |
| --- | --- |
| [] | 3434 |
| ['exact'] | 816 |
| ['budget.trim'] | 586 |
| ['segment.glued'] | 75 |
| ['strip.ops', 'budget.trim'] | 44 |
| ['skip.non_english'] | 34 |
| ['budget.trim', 'segment.glued'] | 33 |
| ['strip.ops', 'clean.query'] | 22 |
| ['unquote.phrases'] | 14 |
| ['strip.ops', 'unquote.phrases', 'clean.query'] | 2 |

### Coverage versus fragmentation

Parseable rewrite-coverage judgments report mean covered-facet ratio `0.599` across `360` sets and redundant=True rate `0.417`. Decomposition depth is the number of distinct `search_branches.branch_role` values per run. Candidate duplication is a proxy: `1 - unique canonical IDs / observed candidate rows`.

| decomposition_depth | runs | mean_nonempty_branch_rate | mean_candidate_rows | mean_unique_canonical | mean_candidate_duplication_rate | mean_labeled_relevance | median_latency_ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| None | 34 | None | 47.0 | 47.0 | 0.0 | None | 0.0 |
| 2 | 2 | 1.0 | None | None | None | None | 41385.92215000426 |
| 3 | 3 | 1.0 | 68.0 | 68.0 | 0.0 | None | 14133.101799990982 |
| 5 | 1 | 1.0 | 71.0 | 71.0 | 0.0 | None | 32594.70099999453 |
| 6 | 1073 | 0.7760173967070518 | 68.74952741020795 | 33.598298676748584 | 0.5359379580352952 | 0.5030226274907126 | 26562.000000005355 |

Interpretation: decomposition helps when coverage/relevance increases without redundancy and latency increasing disproportionately. This export has partial labels, so candidate volume is not treated as quality and empty label cells remain missing rather than zero.

## 4. Provider candidates

Provider-call metrics use `13034` `provider_calls` rows. Nonempty rate = calls with `num_results_returned > 0` / calls; success rate = status=success / calls; latency-adjusted yield = total returned candidates / total latency seconds. Provider raw scores are not compared across providers. Final-result labels matched `420` rows by `(run_key, raw_url=link)`.

Observed provider leaders are criterion-dependent: `ddg` has the highest nonempty-call rate (0.966, n=1489); `search_router` has the highest latency-adjusted candidate yield (7.600/s); `search_router` has the highest labeled positive rate (0.765, n=17); `brave` leads among providers with at least 50 labeled slots (0.747, n=95).

Top nonempty-rate providers (minimum 20 calls):

| provider | calls | success_rate | nonempty_rate | mean_candidates | median_latency_ms | p95_latency_ms | selection_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ddg | 1489 | 0.9905977165883143 | 0.9664204163868368 | 13.014775016789791 | 1781.9999999992433 | 4023.483800003305 | 0.9775179856115108 |
| brave | 1070 | 0.974766355140187 | 0.9551401869158879 | 9.048598130841121 | 867.1253500215244 | 4968.9999999827705 | 0.9775179856115108 |
| search_router | 270 | 0.9962962962962963 | 0.9444444444444444 | 12.925925925925926 | 1160.22654999324 | 4702.999999979511 | 0.24640287769784175 |
| tavily | 476 | 0.9600840336134454 | 0.9390756302521008 | 13.176470588235293 | 4775.607700023102 | 7839.044399999921 | 0.43615107913669066 |
| exa | 416 | 0.9543269230769231 | 0.9302884615384616 | 13.132211538461538 | 2130.364050000935 | 5524.412699975073 | 0.3767985611510792 |
| composio_llm_search | 651 | 0.8755760368663594 | 0.858678955453149 | 11.572964669738864 | 4242.370500000106 | 9119.965100006084 | 0.6007194244604317 |
| searxng | 1490 | 0.9 | 0.8550335570469799 | 9.819463087248321 | 6205.788249998022 | 12309.47770003695 | 0.9775179856115108 |
| langsearch | 792 | 0.8257575757575758 | 0.8207070707070707 | 7.916666666666667 | 2849.5286000071474 | 7625.0 | 0.7275179856115108 |
| serper | 543 | 0.9373848987108656 | 0.7808471454880295 | 6.556169429097606 | 1336.7046999992453 | 3768.2482000091113 | 0.49370503597122306 |
| degoog | 1244 | 0.8014469453376206 | 0.5546623794212219 | 6.521704180064309 | 10589.38935000333 | 13590.179399994668 | 0.8696043165467626 |
| qdrant | 1482 | 0.6261808367071525 | 0.5 | 6.008771929824562 | 1958.7842499986436 | 15015.524099995673 | 0.9775179856115108 |
| brightdata | 255 | 0.7490196078431373 | 0.39215686274509803 | 3.2705882352941176 | 8062.999999994645 | 18719.000000011874 | 0.23741007194244607 |
| serpapi | 579 | 0.2538860103626943 | 0.18825561312607944 | 1.464594127806563 | 1828.0000000086147 | 19193.447700003162 | 0.5305755395683454 |
| google_discovery_engine | 160 | 0.16875 | 0.16875 | 0.83125 | 1483.2630499877268 | 18367.658000002848 | 0.07194244604316546 |
| brightdata_bing | 387 | 0.2661498708010336 | 0.046511627906976744 | 0.36175710594315247 | 18016.00000000326 | 19321.25740000629 | 0.35701438848920863 |

Top latency-adjusted candidate yield:

| provider | calls | candidates_per_second | nonempty_rate | median_latency_ms |
| --- | --- | --- | --- | --- |
| search_router | 270 | 7.599981848110014 | 0.9444444444444444 | 1160.22654999324 |
| exa | 416 | 5.46324015748036 | 0.9302884615384616 | 2130.364050000935 |
| brave | 1070 | 5.313471551012034 | 0.9551401869158879 | 867.1253500215244 |
| serper | 543 | 3.8041960961877814 | 0.7808471454880295 | 1336.7046999992453 |
| tavily | 476 | 2.7733715531376197 | 0.9390756302521008 | 4775.607700023102 |
| composio_llm_search | 651 | 2.4571704599167203 | 0.858678955453149 | 4242.370500000106 |
| langsearch | 792 | 2.242602392700692 | 0.8207070707070707 | 2849.5286000071474 |
| degoog | 1244 | 0.7030609520889436 | 0.5546623794212219 | 10589.38935000333 |
| ddg | 1489 | 0.6605150304634718 | 0.9664204163868368 | 1781.9999999992433 |
| searxng | 1490 | 0.4265787239636271 | 0.8550335570469799 | 6205.788249998022 |

Top labeled positive final-result rate (minimum five labeled slots):

| provider | labeled_final_slots | positive_label_rate | mean_label | median_final_rank |
| --- | --- | --- | --- | --- |
| search_router | 17 | 0.7647058823529411 | 0.5686274509803921 | 6.0 |
| brave | 95 | 0.7473684210526316 | 0.6596491228070175 | 5.0 |
| ddg | 165 | 0.7151515151515152 | 0.5131313131313131 | 7.0 |
| searxng | 40 | 0.7 | 0.5333333333333333 | 8.5 |
| composio_llm_search | 137 | 0.656934306569343 | 0.5255474452554745 | 6.0 |
| langsearch | 46 | 0.6086956521739131 | 0.39855072463768115 | 8.0 |
| degoog | 19 | 0.5789473684210527 | 0.4385964912280702 | 7.0 |
| qdrant | 8 | 0.25 | 0.16666666666666666 | 10.5 |
| brightdata | 6 | 0.16666666666666666 | 0.16666666666666666 | 8.5 |
| tavily | 7 | 0.14285714285714285 | 0.14285714285714285 | 7.0 |

Per-intent leader by nonempty rate (minimum 20 calls per provider; ties break on success rate, then calls):

| intent | provider | calls | success_rate | nonempty_rate | mean_candidates | median_latency_ms |
| --- | --- | --- | --- | --- | --- | --- |
| general | ddg | 883 | 0.9920724801812004 | 0.9773499433748585 | 13.12797281993205 | 1864.8103999439627 |
| ai_coding_and_infrastructure | ddg | 511 | 0.9863013698630136 | 0.9843444227005871 | 13.285714285714286 | 1813.0000000019209 |
| comparison | ddg | 65 | 1.0 | 1.0 | 13.892307692307693 | 1405.5440000011004 |

Sparse provider×intent cohorts below 20 calls are excluded from the leader table. Provider comparisons are observational: provider assignment, branch role, retries, cache state, and query intent are not randomized.

## 5. Broken, orphaned, test, and deprecated schema evidence

This is the only database-wide section. Every one of `42` exported tables and every column was checked. A column is listed when all values are null, non-null values are constant (`unique_non_null <= 1`), or its name contains `test`, `tmp`, `deprecated`, `__`, `hnsw`, or `internal_table`. Content markers are counted only in String columns whose names end with `query`, `title`, `url`, `domain`, `name`, `key`, `repository`, or `tool_name`; matches are heuristic evidence, not proof of test data.

Name-marked tables (content-marker count shown separately):

| table | rows | columns | name_marker | content_marker_count |
| --- | --- | --- | --- | --- |
| _hnsw_test | 1 | 2 | True | 0 |
| flock_config_flockmtl_model_user_defined_internal_table | 2 | 4 | True | 0 |
| flock_config_flockmtl_prompt_internal_table | 4350 | 4 | True | 0 |

Content-marker evidence in selected identifier-like text columns:

| table | rows | columns | content_marker_count | content_marker_examples |
| --- | --- | --- | --- | --- |
| candidate_embeddings | 80 | 7 | 1 | title=Test of Time Award |
| candidate_stage_events | 39461 | 13 | 95 | run_key=test-run ; run_key=test-run ; run_key=test-run |
| code_search_diagnostics | 6053 | 11 | 40 | query="test_files" ".docx" OR "test.pptx" OR "greek.xlsx" repo:SheetJS/test_files ; query=test.pptx repo:SheetJS/test_files ; query="test_files" ".docx" repo:python-openxml/python-docx OR "test.pptx" repo:scanny/python-pptx OR "greek.xlsx" repo:SheetJS/test_files |
| code_search_hits | 8526 | 32 | 135 | url=https://github.com/synthetic-sciences/delphi/blob/3dc453bc8493411ae97cd6bfbeaaa243bda96cc6/docs/env-advanced.md ; url=https://github.com/bgauryy/octocode/blob/af20f667fd2536c9502f69d99fe6bdedfcc839cb/packages/octocode-mcp/tests/security/all-tools-sanitization.test.ts ; url=https://github.com/bga |
| code_search_runs | 350 | 49 | 12 | query="test_files" ".docx" repo:python-openxml/python-docx OR "test.pptx" repo:scanny/python-pptx OR "greek.xlsx" repo:SheetJS/test_files ; query=extension:xlsx test file sample workbook ; query=subtitle fixture WebVTT SRT MHTML |
| final_results | 15378 | 14 | 283 | title=Best Python testing tools 2026 ; title=Python Testing Tutorials – Real Python ; title=11 Best Python Testing Frameworks To Look For In 2026 |
| judge_evaluations | 91 | 28 | 6 | run_key=test-run-001 ; run_key=test-fallback ; run_key=test-run-001 |
| provider_calls | 13034 | 22 | 802 | branch_query=+fastmcp +v3 +enable +disable +components +parameter -test intitle:"MCP" 2026 ; branch_query=Identify and summarize the features of FastMCP version 3 for Python as of 2026 to test the end‑to‑end functionality of the web search tool. ; branch_query=Identify and summarize the features of  |
| provider_results | 37349 | 15 | 285 | raw_url=https://blog.cloudflare.com/live-preview-build-and-test-workers-faster-with-wrangler-cli-1-2-0/ ; raw_url=https://blog.cloudflare.com/live-preview-build-and-test-workers-faster-with-wrangler-cli-1-2-0 ; raw_url=https://github.com/networkx/networkx/actions?query=workflow:test |
| query_transforms | 5060 | 13 | 24 | original_query=site:github.com/networkx/networkx birank import scipy link_analysis.py executing BiRank imports SciPy Validate the local smoke-test supported dependency boundary BiRank imports SciPy ; original_query=site:github.com/networkx/networkx birank import scipy link_analysis.py executing BiRa |
| quick_web_search_citations | 2986 | 9 | 9 | title=GitHub - husniadil/fastmcp-builder: A comprehensive Claude Code skill for building production-ready MCP servers using FastMCP. Includes reference guides, runnable examples, and a complete implementation with OAuth, testing, and best practices. · GitHub ; title=You.com | Best Web Search APIs fo |
| rerank_candidates | 90174 | 24 | 922 | run_key=hang-test ; run_key=hang-test ; run_key=hang-test |
| result_catalog | 22678 | 7 | 76 | canonical_url=https://example.test/x ; canonical_url=https://github.com/networkx/networkx/blob/main/.github/workflows/test.yml ; canonical_url=https://www.patronus.ai/llm-testing/llm-as-a-judge |
| result_labels | 420 | 14 | 13 | raw_url=https://www.britannica.com/dictionary/test ; raw_url=https://foldoc.org/test ; raw_url=https://en.wikipedia.org/wiki/Test |
| search_branches | 6456 | 15 | 252 | branch_query=+fastmcp +v3 +enable +disable +components +parameter -test intitle:"MCP" 2026 ; branch_query=Identify and summarize the features of FastMCP version 3 for Python as of 2026 to test the end‑to‑end functionality of the web search tool. ; branch_query=Identify and summarize the features of  |
| search_candidates | 72769 | 12 | 1772 | title=LiteLLM MCP Test Endpoints: Preview Equals Execute ; title=Nous Research releases NousCoder-14B as an open coding model, testing open alternatives in the Claude Code boom ; title=Nous Research releases NousCoder-14B as an open coding model, testing open alternatives in the Claude Code boom |
| search_runs | 1113 | 35 | 19 | query=test phoenix trace ; query=best practices for microservices testing 2026 ; query=synthetic dataset generation LLM query intent training web search 2026 |
| tool_calls | 11423 | 25 | 35 | query=test ; query=test ; query=test |
| tool_output_items | 10427 | 12 | 72 | raw_url=https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf ; raw_url=https://raw.githubusercontent.com/scanny/python-pptx/master/tests/test_files/test.pptx ; raw_url=https://raw.githubusercontent.com/vid-factory/convertagent/main/test-assets/input/sample.xlsx |

All flagged columns:

| table | column | dtype | rows | non_null | null_count | unique_non_null | sample | name_marker |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| _hnsw_test | id | Int32 | 1 | 1 | 0 | 1 | 1 | False |
| _hnsw_test | emb | List(Float32) | 1 | 1 | 0 | 1 | [0.10000000149011612, 0.20000000298023224, 0.30000001192092896, 0.4000000059604645] | False |
| candidate_embeddings | model_id | String | 80 | 80 | 0 | 1 | 'intfloat/multilingual-e5-large-instruct' ; 'intfloat/multilingual-e5-large-instruct' ; 'intfloat/multilingual-e5-large-instruct' | False |
| candidate_stage_events | entered | Boolean | 39461 | 39461 | 0 | 1 | False ; False ; False | False |
| code_search_hit_variants | association_index | Int32 | 8524 | 8524 | 0 | 1 | 0 ; 0 ; 0 | False |
| code_search_hit_variants | variant_index | Int32 | 8524 | 0 | 8524 | 1 |  | False |
| code_search_hits | symbol_count | Int32 | 8526 | 8526 | 0 | 1 | 0 ; 0 ; 0 | False |
| code_search_hits | payload_json | Binary | 8526 | 0 | 8526 | 1 |  | False |
| code_search_providers | duration_ms | Float64 | 1211 | 0 | 1211 | 1 |  | False |
| code_search_providers | error_type | String | 1211 | 0 | 1211 | 1 |  | False |
| code_search_providers | error_message | String | 1211 | 0 | 1211 | 1 |  | False |
| code_search_repositories | fork | Boolean | 933 | 933 | 0 | 1 | False ; False ; False | False |
| code_search_repositories | payload_json | Binary | 933 | 0 | 933 | 1 |  | False |
| code_search_rerank | status | String | 292 | 292 | 0 | 1 | 'success' ; 'success' ; 'success' | False |
| code_search_rerank | diagnostic_outcome | String | 292 | 0 | 292 | 1 |  | False |
| code_search_rerank | diagnostic_message | String | 292 | 0 | 292 | 1 |  | False |
| code_search_runs | session_id | String | 350 | 0 | 350 | 1 |  | False |
| code_search_runs | planner_exa_semantic_query | String | 350 | 0 | 350 | 1 |  | False |
| content_operations | session_id | String | 2636 | 0 | 2636 | 1 |  | False |
| content_operations | input_count | Int32 | 2636 | 0 | 2636 | 1 |  | False |
| content_operations | error_type | String | 2636 | 0 | 2636 | 1 |  | False |
| content_summaries | item_index | Int32 | 16 | 16 | 0 | 1 | 0 ; 0 ; 0 | False |
| content_summaries | focus_query | String | 16 | 0 | 16 | 1 |  | False |
| content_summaries | source_url_count | Int32 | 16 | 16 | 0 | 1 | 1 ; 1 ; 1 | False |
| content_summaries | is_batch | Boolean | 16 | 16 | 0 | 1 | False ; False ; False | False |
| content_summaries | batch_size | Int32 | 16 | 16 | 0 | 1 | 1 ; 1 ; 1 | False |
| content_summaries | is_stub | Boolean | 16 | 16 | 0 | 1 | False ; False ; False | False |
| content_summaries | model_requested | String | 16 | 0 | 16 | 1 |  | False |
| content_summaries | fallback_attempted | Boolean | 16 | 0 | 16 | 1 |  | False |
| content_summaries | fallback_tier | Int32 | 16 | 0 | 16 | 1 |  | False |
| content_summaries | output_tokens | Int32 | 16 | 0 | 16 | 1 |  | False |
| content_summaries | total_tokens | Int32 | 16 | 0 | 16 | 1 |  | False |
| content_summaries | error_type | String | 16 | 0 | 16 | 1 |  | False |
| final_results | entities_count | Int32 | 15378 | 15378 | 0 | 1 | 0 ; 0 ; 0 | False |
| final_results | payload_json | Binary | 15378 | 15378 | 0 | 1 | b'{}' ; b'{}' ; b'{}' | False |
| flock_config_flockmtl_model_user_defined_internal_table | provider_name | String | 2 | 2 | 0 | 1 | 'openai' ; 'openai' | False |
| flock_config_flockmtl_model_user_defined_internal_table | model_args | Binary | 2 | 2 | 0 | 1 | b'{}' ; b'{}' | False |
| gemini_search_sources | source_kind | String | 725 | 725 | 0 | 1 | 'grounding_source' ; 'grounding_source' ; 'grounding_source' | False |
| judge_evaluations | link | String | 91 | 0 | 91 | 1 |  | False |
| judge_evaluations | cost_usd | Float64 | 91 | 0 | 91 | 1 |  | False |
| judge_evaluations | relevance_raw | Int32 | 91 | 0 | 91 | 1 |  | False |
| judge_evaluations | relevance_scale | String | 91 | 0 | 91 | 1 |  | False |
| llm_call_log | payload_json | Binary | 551 | 0 | 551 | 1 |  | False |
| llm_judgments | input_tokens | Int32 | 9771 | 0 | 9771 | 1 |  | False |
| llm_judgments | output_tokens | Int32 | 9771 | 0 | 9771 | 1 |  | False |
| llm_judgments | rubric_version | String | 9771 | 9771 | 0 | 1 | 'v1' ; 'v1' ; 'v1' | False |
| provider_calls | num_results_requested | Int32 | 13034 | 13034 | 0 | 1 | 15 ; 15 ; 15 | False |
| provider_calls | payload_json | Binary | 13034 | 13034 | 0 | 1 | b'{}' ; b'{}' ; b'{}' | False |
| provider_calls | retry_after_seconds | Float64 | 13034 | 0 | 13034 | 1 |  | False |
| provider_results | is_eligible | Boolean | 37349 | 37349 | 0 | 1 | True ; True ; True | False |
| provider_results | rejection_reason | String | 37349 | 0 | 37349 | 1 |  | False |
| provider_results | payload_json | Binary | 37349 | 0 | 37349 | 1 |  | False |
| query_embeddings | model_id | String | 257 | 257 | 0 | 1 | 'intfloat/multilingual-e5-large-instruct' ; 'intfloat/multilingual-e5-large-instruct' ; 'intfloat/multilingual-e5-large-instruct' | False |
| query_embeddings | payload_json | Binary | 257 | 257 | 0 | 1 | b'{"dim":1024}' ; b'{"dim":1024}' ; b'{"dim":1024}' | False |
| query_understanding_events | final_intent | String | 7 | 7 | 0 | 1 | 'general' ; 'general' ; 'general' | False |
| query_understanding_events | confidence_threshold | Float64 | 7 | 7 | 0 | 1 | 0.5 ; 0.5 ; 0.5 | False |
| query_understanding_events | entities_json | Binary | 7 | 7 | 0 | 1 | b'[]' ; b'[]' ; b'[]' | False |
| query_understanding_events | preserved_terms | List(String) | 7 | 7 | 0 | 1 | [] ; [] ; [] | False |
| query_understanding_events | compared_entities | List(String) | 7 | 7 | 0 | 1 | [] ; [] ; [] | False |
| query_understanding_events | time_sensitivity | String | 7 | 7 | 0 | 1 | 'none' ; 'none' ; 'none' | False |
| query_understanding_events | domain_hints | List(String) | 7 | 7 | 0 | 1 | [] ; [] ; [] | False |
| query_understanding_events | should_decompose | Boolean | 7 | 7 | 0 | 1 | False ; False ; False | False |
| query_variants | selected | Boolean | 2622 | 2622 | 0 | 1 | True ; True ; True | False |
| rerank_candidates | bm25_score | Float64 | 90174 | 0 | 90174 | 1 |  | False |
| rerank_candidates | bm25_rank | Int32 | 90174 | 0 | 90174 | 1 |  | False |
| rerank_candidates | bi_encoder_rank | Int32 | 90174 | 0 | 90174 | 1 |  | False |
| rerank_candidates | fused_score | Float64 | 90174 | 0 | 90174 | 1 |  | False |
| rerank_candidates | entity_overlap_score | Float64 | 90174 | 0 | 90174 | 1 |  | False |
| rerank_candidates | diversity_removed | Boolean | 90174 | 90174 | 0 | 1 | False ; False ; False | False |
| rerank_candidates | diversity_penalty | Float64 | 90174 | 0 | 90174 | 1 |  | False |
| rerank_stages | score_threshold | Float64 | 3252 | 0 | 3252 | 1 |  | False |
| rerank_stages | alpha_blend | Float64 | 3252 | 0 | 3252 | 1 |  | False |
| rerank_stages | failed_passes | Int32 | 3252 | 0 | 3252 | 1 |  | False |
| result_labels | stage | String | 420 | 420 | 0 | 1 | 'final' ; 'final' ; 'final' | False |
| result_labels | source | String | 420 | 420 | 0 | 1 | 'llm_judge' ; 'llm_judge' ; 'llm_judge' | False |
| result_labels | annotator_id | String | 420 | 420 | 0 | 1 | 'judge_quality' ; 'judge_quality' ; 'judge_quality' | False |
| result_labels | rubric_version | String | 420 | 420 | 0 | 1 | 'v1' ; 'v1' ; 'v1' | False |
| result_labels | notes | String | 420 | 0 | 420 | 1 |  | False |
| search_branches | max_results | Int32 | 6456 | 6456 | 0 | 1 | 15 ; 15 ; 15 | False |
| search_branches | skipped_providers | List(String) | 6456 | 6456 | 0 | 1 | [] ; [] ; [] | False |
| search_branches | payload_json | Binary | 6456 | 6456 | 0 | 1 | b'{}' ; b'{}' ; b'{}' | False |
| search_quality_scores | ndcg_at_10 | Float64 | 458 | 0 | 458 | 1 |  | False |
| search_quality_scores | payload_json | Binary | 458 | 0 | 458 | 1 |  | False |
| search_runs | num_results_requested | Int32 | 1113 | 1113 | 0 | 1 | 15 ; 15 ; 15 | False |
| search_runs | skipped_providers | List(String) | 1113 | 1113 | 0 | 1 | [] ; [] ; [] | False |
| search_runs | brave_spellcheck | String | 1113 | 0 | 1113 | 1 |  | False |
| tool_calls | input_tokens | Int32 | 11423 | 0 | 11423 | 1 |  | False |
| tool_calls | output_tokens | Int32 | 11423 | 0 | 11423 | 1 |  | False |
| tool_calls | run_key | String | 11423 | 0 | 11423 | 1 |  | False |
| tool_output_items | run_key | String | 10427 | 0 | 10427 | 1 |  | False |

Checked orphan keys:

Orphan counts are cross-table key mismatches against selected parent sets. Separate namespaces, legacy rows, and differing key domains can produce these counts, so they are evidence for reconciliation rather than proof that rows are broken. Downstream label matching uses `(run_key, raw_url=link)` because canonical IDs do not align across all tables.

| table | key | non_null | orphan_count | orphan_rate |
| --- | --- | --- | --- | --- |
| candidate_stage_events | run_key | 39461 | 2910 | 0.0737436963077469 |
| candidate_stage_events | canonical_result_id | 39461 | 8521 | 0.215934720356808 |
| final_results | canonical_result_id | 6998 | 18 | 0.002572163475278651 |
| judge_evaluations | run_key | 91 | 91 | 1.0 |
| llm_call_log | run_key | 551 | 91 | 0.16515426497277677 |
| provider_results | provider_call_id | 37349 | 39 | 0.0010442046641141664 |
| provider_results | branch_id | 37349 | 118 | 0.003159388470909529 |
| provider_results | canonical_result_id | 37349 | 3918 | 0.10490240702562317 |
| query_transforms | provider_call_id | 5060 | 9 | 0.0017786561264822134 |
| query_transforms | branch_id | 5060 | 21 | 0.004150197628458498 |
| query_understanding_events | run_key | 1 | 1 | 1.0 |
| query_variants | branch_id | 2622 | 22 | 0.008390541571319604 |
| rerank_candidates | run_key | 90174 | 10238 | 0.11353605252068223 |
| rerank_candidates | canonical_result_id | 90174 | 46043 | 0.5106017255528201 |
| result_catalog | canonical_result_id | 22678 | 4 | 0.00017638239703677574 |
| result_labels | canonical_result_id | 420 | 365 | 0.8690476190476191 |
| tool_output_items | canonical_result_id | 10427 | 3960 | 0.37978325501102905 |

The writer-lineage check searched table-name literals under `src\kindly_web_search_mcp_server\analytics\writers`. No literal occurrence was found for: `_hnsw_test, flock_config_flockmtl_model_user_defined_internal_table, flock_config_flockmtl_prompt_internal_table`. That is a conservative dynamic-writer warning, not proof of dead code. `_hnsw_test`, internal configuration tables, and test-looking catalog rows require explicit application-owner review before deletion.

## 6. Last-week latency correlation

Window: `2026-09-08 19:49:01.308270+00:00` through `2026-09-15 19:49:01.308270+00:00` UTC, anchored to the observed maximum `search_runs.recorded_at`; `122` runs, of which `117` are successful. Total latency is `search_runs.duration_ms`.

All-status duration includes `5` non-success rows; `5` of those have zero stored duration, so all-status correlations are descriptive and potentially censored.

| n | median_ms | p95_ms | p99_ms | max_ms |
| --- | --- | --- | --- | --- |
| 122 | 25601.37129998475 | 72390.29019999725 | 101613.96979998972 | 109012.40620001045 |

Success-only duration summary (`status='success'`):

| n | median_ms | p95_ms | p99_ms | max_ms |
| --- | --- | --- | --- | --- |
| 117 | 26854.637200012803 | 72390.29019999725 | 101613.96979998972 | 109012.40620001045 |

All-status Pearson and Spearman correlations (pairwise non-null n):

| feature | n | pearson | spearman |
| --- | --- | --- | --- |
| query_chars | 122 | 0.05971693584178973 | 0.1437294992683011 |
| query_tokens | 122 | 0.07370072503462527 | 0.08449961481491235 |
| content_tokens | 122 | 0.028397093407277394 | 0.07240070384270557 |
| understanding_confidence | 122 | 0.1039912085411664 | 0.16550233639397943 |
| provider_count | 122 | 0.32220387717720295 | 0.22596074631012036 |
| branch_count | 122 | None | None |
| decomposition_depth | 122 | None | None |
| rewrite_count | 122 | None | None |
| rewrite_list_count | 122 | 0.18633247641792192 | 0.2802355880939168 |
| candidate_count | 122 | 0.19119610314913354 | 0.1847301403686826 |
| candidate_rows_observed | 117 | 0.06110895380763679 | 0.11114235281258288 |
| final_result_count | 122 | 0.21510065667982406 | 0.2226430537611251 |
| provider_call_count | 122 | -0.02237645388452598 | 0.03292485276338236 |
| provider_error_rate | 122 | -0.049382637863109924 | 0.14262050164093026 |
| retry_count | 122 | -0.04787408017533704 | -0.021995576989937485 |
| rewrite_input_tokens | 101 | -0.16478653095497983 | -0.12896123299575019 |
| rewrite_output_tokens | 101 | 0.5913279395139022 | 0.3856372688014621 |
| rewrite_latency_ms | 101 | 0.666221938869051 | 0.5076504617054703 |

Success-only Pearson and Spearman correlations:

| feature | n | pearson | spearman |
| --- | --- | --- | --- |
| query_chars | 117 | 0.012151327071283663 | 0.09678985013305264 |
| query_tokens | 117 | 0.040931612626554086 | 0.04519175971700735 |
| content_tokens | 117 | -0.0013900905578225532 | 0.04050881925885406 |
| understanding_confidence | 117 | 0.1038902800531072 | 0.1705399927615825 |
| provider_count | 117 | 0.08129686518176527 | 0.10131068866474967 |
| branch_count | 117 | None | None |
| decomposition_depth | 117 | None | None |
| rewrite_count | 117 | None | None |
| rewrite_list_count | 117 | 0.22833717341041398 | 0.33433613699483067 |
| candidate_count | 117 | 0.0164124354234313 | 0.07772660286497417 |
| candidate_rows_observed | 116 | 0.036137834325573166 | 0.08979906510423959 |
| final_result_count | 117 | -0.14977898505419215 | -0.08538132491783657 |
| provider_call_count | 117 | 0.0005357238034639648 | 0.08433530164361622 |
| provider_error_rate | 117 | 0.2589174811114449 | 0.2586193811192533 |
| retry_count | 117 | -0.05924095307024264 | -0.03318987586806748 |
| rewrite_input_tokens | 96 | -0.1589076866637467 | -0.12886793762263302 |
| rewrite_output_tokens | 96 | 0.6677476217876226 | 0.46062427736190087 |
| rewrite_latency_ms | 96 | 0.691874453427838 | 0.5605029876891514 |

Categorical query/intent associations:
**intent**

| intent | n | median_ms | p95_ms | mean_ms |
| --- | --- | --- | --- | --- |
| comparison | 11 | 34883.64129999536 | 101613.96979998972 | 42368.62832727242 |
| digital_humanities | 9 | 21209.27870000014 | 79073.83069999923 | 29635.712088893342 |
| news | 2 | 58788.97675000189 | 78853.26150000037 | 58788.97675000189 |
| general | 56 | 23915.33875001187 | 71215.91250000347 | 30507.76536428644 |
| ai_coding_and_infrastructure | 44 | 25769.527349999407 | 65770.63949999865 | 30246.673352272905 |

**shape**

| shape | n | median_ms | p95_ms | mean_ms |
| --- | --- | --- | --- | --- |
| question | 5 | 31269.867900002282 | 101613.96979998972 | 45028.87452000141 |
| keyword_long | 114 | 25601.37129998475 | 71905.07540000544 | 31272.203726316468 |
| imperative | 1 | 61316.96739999461 | 61316.96739999461 | 61316.96739999461 |
| keyword_short | 1 | 22387.132700008806 | 22387.132700008806 | 22387.132700008806 |
| url_like | 1 | 15763.064300001133 | 15763.064300001133 | 15763.064300001133 |

**script**

| script | n | median_ms | p95_ms | mean_ms |
| --- | --- | --- | --- | --- |
| Latin | 122 | 25601.37129998475 | 72390.29019999725 | 31882.31771967286 |

**rewrite_enabled**

| rewrite_enabled | n | median_ms | p95_ms | mean_ms |
| --- | --- | --- | --- | --- |
| True | 102 | 28846.89680000156 | 78853.26150000037 | 34408.00105686332 |
| False | 20 | 17919.567650009412 | 30962.63490000274 | 19001.332700001512 |

**status**

| status | n | median_ms | p95_ms | mean_ms |
| --- | --- | --- | --- | --- |
| success | 117 | 26854.637200012803 | 72390.29019999725 | 33244.80992991529 |
| error | 1 | 0.0 | 0.0 | 0.0 |
| cancelled | 4 | 0.0 | 0.0 | 0.0 |

Nonlinear quartile associations:

**query_tokens**

| bucket | n | median_ms | p95_ms |
| --- | --- | --- | --- |
| Q2 | 31 | 31269.867900002282 | 79073.83069999923 |
| Q4 | 27 | 24451.026800001273 | 60470.9634000028 |
| Q3 | 25 | 34444.73499999731 | 68790.83329999412 |
| Q1 | 39 | 22387.132700008806 | 50387.20339999418 |

**query_chars**

| bucket | n | median_ms | p95_ms |
| --- | --- | --- | --- |
| Q2 | 30 | 24089.484800002538 | 84770.46600000176 |
| Q4 | 31 | 27429.159599996638 | 65770.63949999865 |
| Q3 | 30 | 32522.18405000167 | 78853.26150000037 |
| Q1 | 31 | 20885.64699998824 | 50387.20339999418 |

**candidate_count**

| bucket | n | median_ms | p95_ms |
| --- | --- | --- | --- |
| Q1 | 31 | 18238.22260001907 | 79073.83069999923 |
| Q3 | 32 | 28405.139000002237 | 60470.9634000028 |
| Q4 | 28 | 25207.558850001078 | 71905.07540000544 |
| Q2 | 31 | 28097.347400034778 | 68790.83329999412 |

Provider presence cohorts (a run is included if `provider_calls` contains the provider):

| provider | runs | median_ms | p95_ms |
| --- | --- | --- | --- |
| tavily | 71 | 26956.262400002743 | 84770.46600000176 |
| serper | 68 | 31464.493200004654 | 84770.46600000176 |
| brave | 122 | 25601.37129998475 | 72390.29019999725 |
| searxng | 122 | 25601.37129998475 | 72390.29019999725 |
| ddg | 122 | 25601.37129998475 | 72390.29019999725 |
| exa | 122 | 25601.37129998475 | 72390.29019999725 |
| qdrant | 122 | 25601.37129998475 | 72390.29019999725 |
| langsearch | 51 | 23329.463699999906 | 71905.07540000544 |
| google_discovery_engine | 80 | 29108.324700002413 | 71905.07540000544 |
| search_router | 50 | 23139.647849999164 | 71905.07540000544 |
| brightdata | 4 | 24082.77055000144 | 29100.284200001624 |

Cache/retry instrumentation discovery:

{
  "cache_paths": [
    [
      "cache_hit",
      358
    ],
    [
      "disable_cache_fallback",
      308
    ]
  ],
  "retry_paths": [
    [
      "error.retryable",
      269
    ],
    [
      "stage_attempts",
      216
    ]
  ]
}

The payload scan found cache/retry-like keys, but those occurrences cannot be joined to `search_runs` by run key in this export; cache-hit correlation is therefore unavailable. Retry count is inferred only from repeated `(run_key, branch_index, provider)` call groups; it is not a server-reported retry counter.

## 7. Exploratory data analysis

UTC-hour query/latency cohorts:

| utc_hour | runs | mean_tokens | median_latency_ms | p95_latency_ms |
| --- | --- | --- | --- | --- |
| 0 | 34 | 8.941176470588236 | 22846.955599998182 | 67841.28600000258 |
| 1 | 35 | 6.285714285714286 | 24085.56619999581 | 30858.99999999674 |
| 2 | 12 | 9.25 | 31650.40160001081 | 67454.93370003533 |
| 3 | 42 | 7.571428571428571 | 24568.06730000244 | 65790.54780001752 |
| 4 | 15 | 8.066666666666666 | 24781.536999998934 | 52793.96589999669 |
| 5 | 16 | 8.0 | 18457.06419999624 | 38520.78920000349 |
| 6 | 17 | 7.176470588235294 | 19661.609900009353 | 45790.88059999049 |
| 7 | 105 | 9.961904761904762 | 31250.9635000024 | 45250.0 |
| 8 | 68 | 10.044117647058824 | 29024.472799996147 | 63620.10880000889 |
| 9 | 47 | 10.72340425531915 | 27750.0 | 50353.108799958136 |
| 10 | 65 | 9.353846153846154 | 24333.874399992055 | 61546.33639999997 |
| 11 | 69 | 8.971014492753623 | 29703.000000008615 | 65267.86500000162 |
| 12 | 60 | 8.933333333333334 | 23306.606100001318 | 34762.21400000031 |
| 13 | 64 | 9.640625 | 36458.69039998797 | 77449.63209994603 |
| 14 | 40 | 11.65 | 22535.40355002042 | 93166.95410001557 |
| 15 | 62 | 9.806451612903226 | 28419.143049999548 | 66689.04469994595 |
| 16 | 63 | 10.428571428571429 | 26609.00000000038 | 71905.07540000544 |
| 17 | 58 | 10.051724137931034 | 25368.73820000221 | 58256.76540000131 |
| 18 | 56 | 7.410714285714286 | 28320.428600010928 | 84770.46600000176 |
| 19 | 76 | 7.894736842105263 | 25016.09695000434 | 40603.13889999816 |
| 20 | 33 | 6.363636363636363 | 28171.99999996228 | 40983.99999999674 |
| 21 | 39 | 9.41025641025641 | 25302.284800010966 | 50076.536800013855 |
| 22 | 27 | 8.074074074074074 | 26281.713499993202 | 65592.54340000189 |
| 23 | 10 | 5.6 | 24252.765899989754 | 30125.0 |

Successful runs with zero stored candidates or final results: `3`. Treat these as reconciliation candidates, not automatic failures.

P99 latency query slice (all-history threshold; not the last-week window):

| recorded_at | run_key | query | intent | duration_ms | status | branch_count | provider_count | candidate_count | rewrite_enabled | rewrite_latency_ms | error_type |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-17 19:00:53.593186+00:00 | b68dc359-db51-4025-a2e6-652627036ad5 | python 3.14 release date | general | 388408.72970000055 | success | 6 | 12 | 83 | True | 372104.57279999903 | None |
| 2026-07-17 19:06:41.476618+00:00 | fdd59047-74b4-4762-bcea-0ffd38ba6085 | python 3.14 release | general | 340403.315900001 | success | 6 | 12 | 75 | True | 320395.9931999998 | None |
| 2026-07-17 18:51:44.518545+00:00 | 074ddc77-e0a4-46f5-826e-0a6868e6d787 | today news | general | 192398.6963999996 | success | 6 | 12 | 63 | True | 169984.2805999997 | None |
| 2026-07-17 18:47:59.267024+00:00 | 2c131c81-99af-48ca-9b9a-b32c9c959d79 | fastmcp v3 enable disable only=True components parameter visiblity filtering | ai_coding_and_infrastructure | 188871.30419999996 | success | 6 | 12 | 0 | True | 138173.2204 | None |
| 2026-07-17 18:51:31.251543+00:00 | 10205441-53b0-435c-92fc-aedb6b6825af | fastmcp python v3 features | general | 153545.31230000066 | success | 6 | 12 | 68 | True | 641.4992999998503 | None |
| 2026-09-09 17:14:26.526831+00:00 | c0f55f0f-8edf-4ca9-8429-7fd306ce9c5a | github "open source" AI podcast generator LLM script TTS two hosts dialogue | ai_coding_and_infrastructure | 109012.40620001045 | success | 6 | 8 | 82 | True | None | None |
| 2026-09-04 14:17:20.420943+00:00 | 8d830b38-c83f-4c37-b52f-744fa7d182b0 | How have EU AI Act high-risk obligations for general-purpose AI models changed since the 2025 Code of Practice, and what do they mean for self-hosted RAG products sold to EU banks? | general | 107106.42850003205 | success | 6 | 8 | 107 | True | 8162.445800029673 | None |
| 2026-09-04 14:15:51.171985+00:00 | 8f92576b-71c8-4efe-a86f-b7a062b42320 | What are the licensing, operational, and vector-search tradeoffs of replacing Elasticsearch with OpenSearch for a multi-tenant logs platform that already uses Kafka and ClickHouse? | general | 105248.31950000953 | success | 6 | 8 | 102 | True | 1986.3262999569997 | None |
| 2026-09-04 11:19:44.359682+00:00 | d00445a7-513a-4d45-852e-2808922ebcb2 | Brave Search API search operators site: filetype quotes | ai_coding_and_infrastructure | 103138.09949997813 | success | 6 | 8 | 78 | True | None | None |
| 2026-09-09 16:46:57.713451+00:00 | ae0c9ab2-7e22-4182-b03c-38dc95acb400 | what is the Google discovery engine for retail media and how does it differ from Vertex AI Search | comparison | 101613.96979998972 | success | 6 | 7 | 109 | True | 18377.14589999814 | None |
| 2026-09-09 16:46:44.350809+00:00 | 915e2ddd-8273-42dd-8de3-72c22d8e06a7 | NVIDIA Q2 FY2026 earnings call results revenue data center segment | general | 96078.97960000264 | success | 6 | 8 | 38 | True | 11288.810300000478 | None |
| 2026-09-04 11:21:42.892712+00:00 | 75fe70c5-a564-45b7-add0-1021330243c5 | "search query" rewriter prompt "web search" LLM "site:" OR "generate search queries" | general | 94385.35769999726 | success | 6 | 8 | 73 | True | None | None |

The most useful drift lens is the daily table in section 1: compare query length, rewrite rate, success rate, and latency together rather than attributing a change to one provider. Provider×intent and decomposition-depth tables expose interaction effects without pretending they are causal experiments.

## Metric formulas and limitations

- Query length = Unicode word-token count and character count of `search_runs.query`; repetition = duplicate lowercase-trimmed `normalized_query`; family = sorted unique non-stopword token signature.
- Token fidelity: precision = |original ∩ rewrite| / |rewrite|; recall = |original ∩ rewrite| / |original|; F1 = harmonic mean; Jaccard = |intersection| / |union|; character similarity is `SequenceMatcher` only. These are not semantic embeddings.
- Provider candidate yield = mean `num_results_returned`; latency-adjusted yield = Σ returned / (Σ `latency_ms` / 1000); candidate duplication proxy = 1 - unique canonical IDs / observed candidate rows; downstream success = `result_labels.label > 0` after `(run_key, raw_url=link)` matching.
- Pearson is linear association; Spearman is Pearson on ranks and captures monotonic nonlinear association. Quartile tables are used to expose thresholds/U-shapes that a single coefficient hides. No p-values or causal effects are claimed.

## External grounding

External research supports using end-to-end Recall@k/MRR/nDCG for retrieval-grounded rewrite evaluation, lexical overlap as a secondary proxy, explicit coverage-versus-redundancy checks for decomposition, and p50/p95/p99 latency with cache/retry confounds reported separately.

- Stanford IR Book ranked retrieval evaluation: https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html
- ReDI decomposition and interpretation: https://arxiv.org/html/2509.06544v4
- MiniELM rewrite evaluation: https://arxiv.org/html/2501.18056v2
- VALUE rewrite-fidelity caveats: https://arxiv.org/html/2504.05321v2
- CONQRR length-aware rewriting: https://aclanthology.org/2022.emnlp-main.679.pdf
- Survey of conversational-search reformulation evaluation: https://arxiv.org/html/2410.15576v2
- Parallel.ai web-search evaluation methodology: https://parallel.ai/blog/how-to-eval-web-search
- Milvus cache benchmarking caveat: https://milvus.io/ai-quick-reference/how-does-caching-affect-benchmarking-results
- DuckDB `EXPORT DATABASE` documentation: https://duckdb.org/docs/current/sql/statements/export
- Kassis, Agarwal, He, Patel, Brueckner, et al., *Scientific Agent Skills* (2026), arXiv:2609.00065: https://doi.org/10.48550/arXiv.2609.00065

## Artifacts

- Current Parquet export: `duckdb_data\analytics\exports\2026-09-15\full_parquet`
- Analysis script: `C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\docs\analysis\search_event_eda_2026-09-15.py`
- Machine-readable metrics: `docs\analysis\search_event_eda_2026-09-15.json`
