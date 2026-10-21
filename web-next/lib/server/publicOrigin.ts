/** A deployment-owned canonical origin, never a user-supplied redirect target. */
export function publicOrigin(value: string | undefined): URL | null {
  if (!value?.trim()) return null;
  const url = new URL(value.trim());
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new Error("MATEQUILL_PUBLIC_URL must be an HTTPS origin");
  return url;
}

export function canonicalRedirect(
  requestURL: string,
  host: string | null,
  forwardedProtocol: string | null,
  configured: string | undefined,
): URL | null {
  const canonical = publicOrigin(configured);
  if (!canonical) return null;
  const incoming = new URL(requestURL);
  let actualHost = "";
  try {
    if (host && !/[\s/?#@]/.test(host))
      actualHost = new URL(`https://${host}`).host;
  } catch {
    /* An invalid authority is redirected only to the fixed origin. */
  }
  // The TLS edge must overwrite X-Forwarded-Proto, and preserve Host. A client
  // header is never used as a destination or as authorization for any resource.
  const https =
    incoming.protocol === "https:" ||
    forwardedProtocol?.toLowerCase() === "https";
  if (actualHost === canonical.host && https) return null;
  canonical.pathname = incoming.pathname;
  canonical.search = incoming.search;
  return canonical;
}
