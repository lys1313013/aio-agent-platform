FROM --platform=$BUILDPLATFORM node:24.22.1-alpine AS build

RUN corepack enable && corepack prepare pnpm@11.1.3 --activate

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

RUN pnpm install --frozen-lockfile

COPY . .

RUN test "$(node --version)" = "v$(cat .nvmrc)" && pnpm run build

FROM nginx:1.31.8-alpine

COPY --from=build /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
