{ ... }:
{
  environment.etc."ente/museum.yaml" = {
    mode = "0644";
    text = ''
      db:
        host: ente-postgres
        port: 5432
        name: ente_db
        user: pguser

      s3:
        are_local_buckets: true
        use_path_style_urls: true

        b2-eu-cen:
          endpoint: localhost:3200
          region: eu-central-2
          bucket: b2-eu-cen

        wasabi-eu-central-2-v3:
          endpoint: localhost:3200
          region: eu-central-2
          bucket: wasabi-eu-central-2-v3
          compliance: false

        scw-eu-fr-v3:
          endpoint: localhost:3200
          region: eu-central-2
          bucket: scw-eu-fr-v3

      apps:
        public-albums: http://localhost:3002
        cast: http://localhost:3004
        public-locker: http://localhost:3005
        public-paste: http://localhost:3008
        embed-albums: http://localhost:3006
        accounts: http://localhost:3001
    '';
  };

  environment.etc."ente/s3-init.sh" = {
    mode = "0755";
    text = ''
      #!/bin/sh
      set -eu

      export AWS_ACCESS_KEY_ID="$(cat /run/secrets/s3_user)"
      export AWS_SECRET_ACCESS_KEY="$(cat /run/secrets/s3_pass)"

      until aws --endpoint-url http://ente-versitygw:3200 s3api list-buckets >/dev/null 2>&1; do
        echo "Waiting for VersityGW..."
        sleep 1
      done

      aws --endpoint-url http://ente-versitygw:3200 s3api create-bucket --bucket b2-eu-cen || true
      aws --endpoint-url http://ente-versitygw:3200 s3api create-bucket --bucket wasabi-eu-central-2-v3 || true
      aws --endpoint-url http://ente-versitygw:3200 s3api create-bucket --bucket scw-eu-fr-v3 || true
    '';
  };
}
