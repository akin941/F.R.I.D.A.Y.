import type { NextConfig } from "next";
import { securityHeaderRules } from "./src/lib/security-headers";

const nextConfig: NextConfig = {
  async headers() {
    return securityHeaderRules(process.env.NODE_ENV === "production");
  },
};

export default nextConfig;
