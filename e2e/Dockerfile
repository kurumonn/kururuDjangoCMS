FROM mcr.microsoft.com/playwright:v1.62.1-noble@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e

WORKDIR /tests

COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit

COPY playwright.config.ts ./
COPY tsconfig.json ./
COPY tests ./tests

ENTRYPOINT ["npx", "playwright", "test"]
