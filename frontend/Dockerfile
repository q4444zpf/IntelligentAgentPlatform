FROM node:20-alpine AS build

ARG VITE_DEV_USER_ID=""
ARG VITE_DEV_PROJECT_ID=""
ARG VITE_DEV_USER_ROLE=""
ENV VITE_DEV_USER_ID=$VITE_DEV_USER_ID
ENV VITE_DEV_PROJECT_ID=$VITE_DEV_PROJECT_ID
ENV VITE_DEV_USER_ROLE=$VITE_DEV_USER_ROLE

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
