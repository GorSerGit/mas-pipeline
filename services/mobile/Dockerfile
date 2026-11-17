FROM python:3.12-slim-trixie

# JDK и лицензии Android SDK — агент MOBILE собирает сгенерированный проект и правит ошибки компиляции.
# Сам SDK (платформу под compileSdk, build-tools) докачивает Android Gradle Plugin, Gradle нужной
# версии — агент (обе версии выбирает Qwen); всё это и кэш Gradle — в /cache, его стоит смонтировать томом.
# Чтобы образ был лёгким:
#   - JDK урезается jlink'ом: без отладчика, jshell, jpackage, локалей и отладочной информации;
#     jlink в нём остаётся — его вызывает Android Gradle Plugin при компиляции Java (BuildConfig);
#   - cmdline-tools нужны только принять лицензии — после этого удаляются вместе с исходным JDK.
ARG ANDROID_CMDLINE_TOOLS=https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
ARG JDK_DROP_MODULES="jdk.hotspot.agent|jdk.jpackage|jdk.jshell|jdk.jdi|jdk.jdwp.agent|jdk.jconsole|jdk.jstatd|jdk.javadoc|jdk.editpad|jdk.localedata|jdk.incubator.vector"
ENV JAVA_HOME=/opt/jdk \
    PATH=/opt/jdk/bin:$PATH \
    ANDROID_BUILD_CACHE=/cache \
    ANDROID_HOME=/cache/android-sdk \
    ANDROID_LICENSES=/opt/android-licenses
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jdk-headless unzip curl \
    && SYSTEM_JDK=$(dirname $(dirname $(readlink -f $(which javac)))) \
    && MODULES=$(java --list-modules | sed 's/@.*//' | grep -v -E "^($JDK_DROP_MODULES)$" | paste -sd, -) \
    && "$SYSTEM_JDK/bin/jlink" --add-modules "$MODULES" --output "$JAVA_HOME" \
         --strip-java-debug-attributes --no-man-pages --no-header-files --compress=zip-6 \
    && rm -f "$JAVA_HOME/lib/security/cacerts" \
    && cp -L /etc/ssl/certs/java/cacerts "$JAVA_HOME/lib/security/cacerts" \
    && curl -fsSL "$ANDROID_CMDLINE_TOOLS" -o /tmp/cmdline-tools.zip \
    && mkdir -p /tmp/sdk/cmdline-tools \
    && unzip -q /tmp/cmdline-tools.zip -d /tmp/sdk/cmdline-tools \
    && mv /tmp/sdk/cmdline-tools/cmdline-tools /tmp/sdk/cmdline-tools/latest \
    && yes | JAVA_HOME="$SYSTEM_JDK" /tmp/sdk/cmdline-tools/latest/bin/sdkmanager --sdk_root=/tmp/sdk --licenses > /dev/null \
    && mv /tmp/sdk/licenses "$ANDROID_LICENSES" \
    && rm -rf /tmp/sdk /tmp/cmdline-tools.zip \
    && apt-get purge -y --auto-remove default-jdk-headless 'openjdk-*' ca-certificates-java java-common unzip curl \
    && rm -rf /var/lib/apt/lists/* /usr/lib/jvm

WORKDIR /app
COPY services/mobile/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ /common/
ENV PYTHONPATH=/common
COPY services/mobile/ .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
