FROM node:20-alpine

WORKDIR /app

RUN apk add --no-cache wget

ARG INTERNAL_API_BASE=http://backend:8000
ARG NEXT_PUBLIC_API_BASE=

ENV INTERNAL_API_BASE=$INTERNAL_API_BASE
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

COPY frontend/ ./

RUN npm run build

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
