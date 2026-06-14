import { S3Client } from "@aws-sdk/client-s3";

export const r2 = new S3Client({
  endpoint: process.env.CLOUDFLARE_R2_ENDPOINT!,
  credentials: {
    accessKeyId:     process.env.CLOUDFLARE_R2_ACCESS_KEY_ID!,
    secretAccessKey: process.env.CLOUDFLARE_R2_SECRET_ACCESS_KEY!,
  },
  region: "auto",
  forcePathStyle: false,
});

export const R2_BUCKET = process.env.CLOUDFLARE_R2_BUCKET!;
