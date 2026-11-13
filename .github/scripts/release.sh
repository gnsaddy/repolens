#!/usr/bin/env bash
# Tag a commit v<version> (if the tag is missing) and publish its GitHub Release (if it is missing).
# Usage: release.sh <version> <commit>. Needs GH_TOKEN and GITHUB_REPOSITORY; run from a full checkout (fetch-depth 0).
# Nothing that exists is changed: a tag is never moved and a Release is never rewritten.
set -euo pipefail
VERSION="$1"; SHA="$2"; TAG="v$VERSION"
OWNER=$(echo "${GITHUB_REPOSITORY_OWNER:-${GITHUB_REPOSITORY%%/*}}" | tr '[:upper:]' '[:lower:]')

if gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG" >/dev/null 2>&1; then
  echo "$TAG exists already, so it is left where it is"
else
  gh api "repos/$GITHUB_REPOSITORY/git/refs" -f ref="refs/tags/$TAG" -f sha="$SHA" >/dev/null
  echo "tagged $SHA as $TAG"
fi

if gh release view "$TAG" >/dev/null 2>&1; then
  echo "the release $TAG exists already, so it is left as it is"; exit 0
fi

ORDER=$( (git tag; echo "$TAG") | sort -uV )
PREVIOUS=$(echo "$ORDER" | awk -v t="$TAG" '$0==t {print prev; exit} {prev=$0}')
LATEST=false; [ "$(echo "$ORDER" | tail -1)" = "$TAG" ] && LATEST=true

NOTES=$(mktemp)
{
  if [ -f "changelog/$TAG.md" ]; then tail -n +2 "changelog/$TAG.md" | sed '1{/^$/d}'; else echo "Release $VERSION."; fi
  echo
  echo "## Get it"
  echo
  echo '```bash'
  echo "docker pull ghcr.io/$OWNER/repolens-api:$VERSION"
  echo "docker pull ghcr.io/$OWNER/repolens-web:$VERSION"
  echo '```'
  echo
  echo "Run it with \`docker compose up --build\` from this tag, or see [the operations guide](docs/operations.md)."
  [ -n "$PREVIOUS" ] && { echo; echo "**Full changelog:** https://github.com/$GITHUB_REPOSITORY/compare/$PREVIOUS...$TAG"; }
} > "$NOTES"

gh release create "$TAG" --verify-tag --title "RepoLens $VERSION" --notes-file "$NOTES" --latest="$LATEST"
echo "published the release $TAG"
