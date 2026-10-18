# QuantAssistant.Api — .NET 10 生产镜像（linux-x64，与目标 Linux 服务器同拓扑）
# 构建：docker build -f backend-dotnet/Dockerfile -t quant-assistant-api:latest backend-dotnet

# ---- 构建阶段 ----
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

# 先还原依赖（利用层缓存）
COPY src/QuantAssistant.Core/QuantAssistant.Core.csproj src/QuantAssistant.Core/
COPY src/QuantAssistant.Infrastructure/QuantAssistant.Infrastructure.csproj src/QuantAssistant.Infrastructure/
COPY src/QuantAssistant.Api/QuantAssistant.Api.csproj src/QuantAssistant.Api/
RUN dotnet restore src/QuantAssistant.Api/QuantAssistant.Api.csproj -r linux-x64

# 复制源码并发布
COPY src/ ./src/
RUN dotnet publish src/QuantAssistant.Api/QuantAssistant.Api.csproj \
    -c Release -r linux-x64 --self-contained false \
    -o /app/publish /p:UseAppHost=false

# ---- 运行阶段 ----
FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app

# 非 root 用户（计划书 §6「非 root、read_only、cap_drop」）
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build --chown=app:app /app/publish ./

USER $APP_UID
EXPOSE 8080
ENV ASPNETCORE_URLS=http://0.0.0.0:8080
ENV DOTNET_EnableDiagnostics=0

ENTRYPOINT ["dotnet", "QuantAssistant.Api.dll"]
