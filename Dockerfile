FROM node:22-alpine AS deps

RUN apk upgrade --no-cache

RUN mkdir /app && chown node:node /app
WORKDIR /app

USER node

ENV NODE_ENV=production

COPY --chown=node:node llm/package*.json ./

RUN if [ -f package-lock.json ]; then \
    npm ci --omit=dev --no-audit --no-fund; \
    else \
    npm install --omit=dev --no-audit --no-fund; \
    fi

FROM gcr.io/distroless/nodejs22-debian12:nonroot

ENV NODE_ENV=production
WORKDIR /app

COPY --from=deps --chown=nonroot:nonroot /app/node_modules ./node_modules
COPY --chown=nonroot:nonroot llm/server.js ./server.js

USER nonroot

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/nodejs/bin/node", "-e", "const http=require('http'); const req=http.get('http://127.0.0.1:3000/health', res=>process.exit(res.statusCode===200?0:1)); req.on('error', ()=>process.exit(1)); req.setTimeout(4000, ()=>{req.destroy(); process.exit(1);});"]

CMD ["server.js"]
