FROM eclipse-temurin:24-jre

#RUN apt update -y &&\
#	rm -rf /var/lib/apt/lists/*

WORKDIR /graphhopper

ADD https://github.com/graphhopper/graphhopper/releases/download/10.2/graphhopper-web-10.2.jar graphhopper.jar

COPY docker/certs/* /usr/local/share/ca-certificates/
RUN update-ca-certificates

RUN keytool -import -noprompt -alias zscaler-corp-cert -trustcacerts -keystore ${JAVA_HOME}/lib/security/cacerts -storepass changeit -file /usr/local/share/ca-certificates/zscaler-root.pem.crt

COPY docker/graphhopper-entrypoint.sh entrypoint.sh

ENTRYPOINT /graphhopper/entrypoint.sh