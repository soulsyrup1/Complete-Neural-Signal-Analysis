# Indexing, Research Discovery, and Publicity Targets

Project URL: https://github.com/soulsyrup1/Complete-Neural-Signal-Analysis

## Automated notifications that are worth using

These are the channels that can actually help crawlers discover changed URLs. Use them only for URLs you control and only when content changes.

| Target | Method | Notes |
|---|---|---|
| Google Search | Google Search Console sitemap submission or Search Console API | Do not use the old unauthenticated Google sitemap ping endpoint. Keep `lastmod` accurate. |
| Bing | IndexNow and Bing Webmaster Tools URL Submission API | Best automated option for changed URLs when ownership can be verified. |
| Yandex | IndexNow | Works through the same IndexNow pattern when ownership can be verified. |
| Baidu | Baidu Search Resource Platform | Use manual/API submission only if you have access and a Chinese-market indexing need. |
| Naver | Naver Search Advisor | Manual search-console style submission for Korean-market visibility. |
| Seznam | Seznam webmaster/search submission | Useful mainly for Czech-market visibility. |
| robots.txt | `Sitemap:` directive | Keep a public sitemap discoverable from the actual hosted website root. |
| WebSub | Atom/RSS hub notification | Useful only if you publish a feed. |

## Do not curl these as fake `/ping` endpoints

Scopus, Web of Science, IEEE Xplore, PubMed, ScienceDirect, JSTOR, Google Scholar, arXiv, DARPA, NIH, NSF, NASA, DOE, SAM.gov, Grants.gov, national labs, universities, and research-funding websites generally do not accept arbitrary `https://example.org/ping?sitemap=...` requests. Pinging them does not submit your project and can look like low-quality automated traffic.

## Research software and open-science discovery targets

| Target | Use for | Action |
|---|---|---|
| Zenodo | DOI and citable GitHub releases | Enable GitHub-Zenodo integration, make a release, add DOI badge and DOI to `CITATION.cff`. |
| Software Heritage | Long-term source-code preservation and SWHID | Trigger Save Code Now or use a GitHub Action on releases. |
| Figshare | DOI for software, datasets, figures, reports | Publish release archives or companion datasets when appropriate. |
| OSF | Open science project page and research collaboration | Create a public project and link the GitHub repository. |
| OpenAIRE | European open-science discovery | Connect DOI-backed outputs/repositories where eligible. |
| DataCite/Crossref | DOI metadata propagation | Usually handled through Zenodo/Figshare/institutional repository. |
| bio.tools | Life-science/bioinformatics tool discovery | Submit if the project is positioned as life-science or biomedical software. |
| Papers with Code | ML paper + code discovery | Add only when you have a paper/preprint and reproducible results or benchmarks. |
| arXiv / preprint servers | Research paper discovery | Publish a methods/software paper that links to the repository. |
| Google Dataset Search | Dataset visibility | Add schema.org `Dataset` JSON-LD to a public landing page. |
| GitHub Topics | GitHub-native discovery | Add topics: `neuroscience`, `eeg`, `signal-processing`, `bci`, `machine-learning`, `computational-neuroscience`, `neurotechnology`, `open-science`, `research-software`. |
| ORCID | Researcher profile discoverability | Add the DOI/software output to contributor ORCID records after Zenodo/Figshare release. |

## Government and funder repositories: submit only when eligible

These are not generic publicity targets. Use them only if the project is connected to a funded publication, grant, dataset, or official deliverable.

| Target | Eligibility note |
|---|---|
| NIH / PubMed Central / Europe PMC | Biomedical manuscripts and grant-related outputs. |
| NASA STI / PubSpace | NASA-funded publications and associated data. |
| DOE OSTI / DOE CODE | DOE-funded publications, datasets, and software. |
| NSF Public Access Repository | NSF-funded accepted manuscripts and outputs. |
| Defense Technical Information Center | Eligible defense-funded technical reports and outputs. |
| EU Open Research / CORDIS / OpenAIRE | EU-funded projects, publications, data, and software outputs. |

## Recommended GitHub repository improvements

1. Add a concise `README.md` opening paragraph with the phrases: neural signal analysis, EEG analysis, AI signal processing, computational neuroscience, BCI research, open-source neuroscience, research software.
2. Add `CITATION.cff`, `codemeta.json`, `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, and release notes.
3. Create a tagged GitHub release, connect it to Zenodo, then add the DOI badge and DOI metadata.
4. Use Software Heritage Save Code Now for release snapshots.
5. Use IndexNow only for a real website or GitHub Pages/custom domain where you can host the IndexNow key file under the same host.
