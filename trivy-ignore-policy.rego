package trivy

import rego.v1

# Ignore LOW and UNKNOWN severity OS vulnerabilities in the base image.
# These are unfixed in Debian 13 Trixie and represent accepted risk.
# HIGH and CRITICAL are handled individually in .trivyignore.
# MEDIUM CVEs escalated to HIGH are handled individually in .trivyignore.
default ignore := false

ignore if input.Severity == "LOW"
ignore if input.Severity == "UNKNOWN"
