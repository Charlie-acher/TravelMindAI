# 部署层：Vue只在构建阶段依赖Node，运行阶段由Nginx提供同源网页和API代理。
FROM public.ecr.aws/docker/library/node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
FROM public.ecr.aws/docker/library/nginx:stable-alpine
COPY deploy/app/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
