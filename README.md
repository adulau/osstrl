# OSSTRL - Open Source Software Technology Readiness Level (OSSTRL) 1–9

An open-source application that estimates an **Open Source Software Technology Readiness Level (OSSTRL) 1–9** from evidence that can be gathered automatically from a GitHub repository.

### OSSTRL vs. traditional TRL

| | OSSTRL | Traditional Technology Readiness Level (TRL) |
|---|---|---|
| **Purpose** | Estimates the readiness and sustainability of an open-source software project | Assesses the maturity of a technology from basic principles through operational use |
| **Primary evidence** | Automatically observable GitHub repository signals | Experimental validation, demonstrations, deployment and operational evidence |
| **Focus** | Community, governance, development, support, security and privacy | Technical maturity in increasingly realistic environments |
| **Output** | A 1–9 estimate plus an evidence score and confidence/coverage | A 1–9 level supported by domain-specific review |
| **Key limitation** | Cannot prove adoption or performance in an operational environment from repository data alone | Does not by itself evaluate open-source project health or sustainability |

OSSTRL complements rather than replaces a formal TRL assessment: it makes open-source project readiness visible using evidence that can be collected consistently from a repository.

The evidence model is **inspired by the [Apereo OSS Health and Sustainability Rubric](https://github.com/apereo/oss-rubric)**. It intentionally does **not** claim to reproduce the complete Apereo assessment: the Apereo rubric contains 40+ criteria, and several important maturity facts cannot be proven from a GitHub repository alone (real-world implementations, actual adoption, funding sustainability, accessibility conformance, deployment environment, etc.).

Instead, this application does the following:

1. collects GitHub-verifiable evidence;
2. maps it into the Apereo areas **Community, Governance, Development, Support, Security/Privacy**;
3. computes a weighted 0–100 evidence score;
4. maps the score to OSSTRL 1–9;
5. applies readiness gates so documentation alone cannot produce a high level;
6. reports evidence coverage/confidence separately from the score.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

A GitHub token is strongly recommended to avoid the low unauthenticated REST API rate limit:

```bash
export GITHUB_TOKEN=github_pat_...
```

## Usage

```bash
python osstrl.py MISP/MISP
python osstrl.py https://github.com/vulnerability-lookup/vulnerability-lookup --format markdown
python osstrl.py apereo/oss-rubric --format json --output report.json
```

Example summary:

```text
owner/project: OSSTRL 7/9 | score 76.4% | confidence 100.0%
```

Or a sample markdown report available at [vulnerability-lookup.md](./samples/vulnerability-lookup.md)

## GitHub evidence currently gathered

### Community

- repository age / years in practice;
- number of non-bot contributors (first 100 returned by GitHub);
- Code of Conduct;
- issue and pull-request responsiveness proxy;
- public repository + contribution guidelines.

### Governance

- `GOVERNANCE`, `MAINTAINERS`, `CODEOWNERS`;
- roadmap artifact;
- issue and pull-request templates;
- `.github/FUNDING.yml`;
- contribution/management documentation.

### Development

- activity in the last year;
- release history, recency, frequency and cadence;
- detected license;
- CI workflows;
- conventional test tree;
- generic integration/standards artifacts such as OpenAPI, Docker and package/build metadata.

### Support

- README + documentation tree;
- changelog and semantic-version-like release tags as a backwards-compatibility communication proxy;
- Issues/Discussions support channels;
- project homepage.

### Security / Privacy

- `SECURITY.md`;
- common repository security automation (Dependabot, CodeQL, dependency review, Trivy, Snyk, Renovate);
- privacy-policy artifact.

## Scoring model

The category weights total 100 points:

- Community: 28
- Governance: 18
- Development: 30
- Support: 14
- Security / Privacy: 10

These weights and the OSSTRL thresholds are **prototype choices, not Apereo's official scoring weights**. They are all explicit in `osstrl.py` so they can be reviewed and changed.

The aggregate score is mapped to 1–9, then gates cap advanced levels when essential evidence is missing. For example, OSSTRL 5+ requires a stable release and multiple contributors; OSSTRL 8+ requires sustained history, governance documentation and a security policy.

## Important limitation

This is better understood as an **OSS repository readiness/maturity estimator** than a formal Technology Readiness Level calculator. A formal TRL 6–9 normally requires evidence about validation, deployment and use in relevant/operational environments. GitHub alone cannot establish that reliably.

For a production system I would add a second evidence channel (`osstrl.yaml`) where maintainers can provide externally verifiable claims such as deployments, user organisations, support commitments, security audits, accessibility audits, and production references. The automatic GitHub score and asserted/verified deployment evidence can then be kept separate.

## Attribution

Rubric concepts and category names are derived from the **Apereo OSS Health and Sustainability Rubric**, licensed CC BY 4.0:

- https://github.com/apereo/oss-rubric

GitHub REST API documentation:

- https://docs.github.com/en/rest

## License

The software is open-source under a 2-clause BSD license.

Copyright 2018-2026 Alexandre Dulaunoy - a@foo.be

Copyright 2026 ossbase.org

Copyright 2026 CIRCL - Computer Incident Response Center Luxembourg

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS “AS IS” AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
