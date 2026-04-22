// Sprawl frontend data layer.
//
// Combines two public endpoints:
//   - SprawlAPI: REST calls to our AWS API for off-chain content
//                (links, entities, arcs, votes, mentions, drafts)
//   - SprawlGraph: GraphQL calls to the Goldsky subgraph for on-chain
//                  state (citizens, collections, sales, marketplace)
//
// Both URLs are public by design. Reads are open; writes are wallet-gated
// via EIP-712 signatures, not URL secrecy. Frontend never touches private
// keys of any kind.

(function (global) {
  "use strict";

  // Override these via window.SPRAWL_API_URL / window.SPRAWL_SUBGRAPH_URL
  // before this script loads, or edit the defaults here at deploy time.
  //
  // The default is CloudFront in front of API Gateway. Reads are served
  // from edge cache per the origin's Cache-Control headers (typically
  // 10–30s), which absorbs repeat traffic and keeps our Lambda pool free
  // for real work. POST writes pass straight through. To bypass the CDN
  // (e.g., when you just wrote and want immediately-fresh reads), point
  // window.SPRAWL_API_URL at https://zujinkdgtj.execute-api.us-east-1.amazonaws.com/dev
  const API_URL = window.SPRAWL_API_URL ||
    "https://d1pdbr4fdk59bz.cloudfront.net";
  const SUBGRAPH_URL = window.SPRAWL_SUBGRAPH_URL ||
    "https://api.goldsky.com/api/public/project_cmo4yujy1v9de01zhfzy88sqs/subgraphs/sprawl-hybrid/0.1.0/gn";

  // --- REST helpers ---------------------------------------------------

  async function rest(path) {
    const url = API_URL.replace(/\/$/, "") + path;
    const r = await fetch(url);
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`api ${path}: ${r.status}`);
    return await r.json();
  }

  // --- GraphQL helpers ------------------------------------------------

  async function graphql(query, variables) {
    const r = await fetch(SUBGRAPH_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables: variables || {} }),
    });
    if (!r.ok) throw new Error(`subgraph: ${r.status}`);
    const body = await r.json();
    if (body.errors) throw new Error("subgraph: " + JSON.stringify(body.errors));
    return body.data || {};
  }

  // --- Public surface -------------------------------------------------

  const SprawlAPI = {
    // Off-chain content
    link:          (id)        => rest(`/links/${id}`),
    linkChildren:  (id)        => rest(`/links/${id}/children`),
    linkContext:   (id)        => rest(`/links/${id}/context`),
    recentLinks:   (lim=50)    => rest(`/feed/recent-links?limit=${lim}`),
    byAuthor:      (a,lim=50)  => rest(`/feed/by-author/${a.toLowerCase()}?limit=${lim}`),
    // Lean link metadata for tree rendering (no text body). Paired with
    // linkContext(id) when the user selects a link — that's when we pay
    // the cost of pulling the full ancestry's text.
    feedTree:      (lim=500)   => rest(`/feed/tree?limit=${lim}`),
    entity:        (id)        => rest(`/entities/${encodeURIComponent(id)}`),
    entities:      (lim=200)   => rest(`/entities?limit=${lim}`),
    entityByType:  (t,lim=50)  => rest(`/entities/by-type/${encodeURIComponent(t)}?limit=${lim}`),
    entityMentions:(id,lim=50) => rest(`/entities/${encodeURIComponent(id)}/mentions?limit=${lim}`),
    entitiesByCreator: (a,lim=200) => rest(`/entities/by-creator/${a.toLowerCase()}?limit=${lim}`),
    arc:           (id)        => rest(`/arcs/${encodeURIComponent(id)}`),
    arcs:          (lim=200)   => rest(`/arcs?limit=${lim}`),
    arcByAnchor:   (l,lim=50)  => rest(`/arcs/by-anchor/${l}?limit=${lim}`),
    arcReferences: (id,lim=50) => rest(`/arcs/${encodeURIComponent(id)}/references?limit=${lim}`),
    arcsByCreator: (a,lim=200) => rest(`/arcs/by-creator/${a.toLowerCase()}?limit=${lim}`),
    votesByLink:   (id,lim=50) => rest(`/votes/by-link/${id}?limit=${lim}`),
    votesByVoter:  (a,lim=50)  => rest(`/votes/by-voter/${a.toLowerCase()}?limit=${lim}`),
    stats:         ()          => rest(`/stats/global`),
    citizenStats:  (a)         => rest(`/citizens/${a.toLowerCase()}/stats`),
    // nextLinkId was removed with the split-signature flow. The server
    // assigns the linkId after validation and returns it in the write
    // response; there's no need to pre-fetch an ID.
    collectPrepare:(k,id)      => rest(`/collect/prepare/${k}/${encodeURIComponent(id)}`),
  };

  const SprawlGraph = {
    // Citizens
    async citizen(address) {
      const { citizen } = await graphql(
        `query($id: ID!) {
          citizen(id: $id) {
            id name isBanned registeredAt registeredAtBlock
            totalCollectedAsCreator totalPurchases totalSales
          }
        }`, { id: address.toLowerCase() });
      return citizen;
    },
    async citizens(first = 100) {
      // The full citizens list is fetched by almost every page just to
      // resolve address → name. It changes only on register / rename / ban
      // events, so a short session cache costs almost nothing and cuts a
      // whole GraphQL round-trip per navigation.
      const cacheKey = `sprawl.citizens.${first}`;
      const TTL_MS   = 60_000;
      try {
        const cached = sessionStorage.getItem(cacheKey);
        if (cached) {
          const { t, v } = JSON.parse(cached);
          if (Date.now() - t < TTL_MS) return v;
        }
      } catch { /* storage disabled — fall through to network */ }
      const { citizens } = await graphql(
        `query($n: Int!) {
          citizens(first: $n, orderBy: registeredAt, orderDirection: desc) {
            id name isBanned registeredAt totalCollectedAsCreator totalSales totalPurchases
          }
        }`, { n: first });
      try {
        sessionStorage.setItem(cacheKey, JSON.stringify({ t: Date.now(), v: citizens }));
      } catch { /* storage full / disabled — skip cache */ }
      return citizens;
    },

    // Collected assets (on-chain state)
    async collectedAsset(id) {
      const { collectedAsset } = await graphql(
        `query($id: ID!) {
          collectedAsset(id: $id) {
            id kind nativeId owner collectedAt authoredAt firstSalePrice listingPrice
            cleared parentLinkId isRecap coversFromId coversToId entityType anchorLinkId
            creator { id name }
          }
        }`, { id });
      return collectedAsset;
    },
    async collectedAssets(first = 100, kind = null) {
      const where = kind ? `(where: { kind: "${kind}" }, ` : "(";
      const { collectedAssets } = await graphql(
        `query($n: Int!) {
          collectedAssets${where}first: $n, orderBy: collectedAt, orderDirection: desc) {
            id kind nativeId owner collectedAt listingPrice cleared
            creator { id name }
          }
        }`, { n: first });
      return collectedAssets;
    },

    // Sales feed
    async recentSales(first = 50) {
      const { sales } = await graphql(
        `query($n: Int!) {
          sales(first: $n, orderBy: timestamp, orderDirection: desc) {
            id firstSale price seller buyer protocolCut sellerCut timestamp
            asset { kind nativeId creator { id name } }
          }
        }`, { n: first });
      return sales;
    },

    // Global stats
    async stats() {
      const { protocolStats } = await graphql(
        `{ protocolStats(id: "global") {
          totalCitizens totalBanned
          totalCollectedLinks totalCollectedEntities totalCollectedArcs
          totalSales totalVolume currentFirstSalePrice currentOperator
        } }`);
      return protocolStats;
    },
  };

  // Convenience: combined view of a citizen. Goldsky for on-chain identity,
  // AWS for their off-chain drafts feed.
  async function citizenFull(address) {
    const [onchain, drafts] = await Promise.all([
      SprawlGraph.citizen(address),
      SprawlAPI.byAuthor(address, 200),
    ]);
    return { onchain, drafts: drafts && drafts.items || [] };
  }

  // Bounded-concurrency map: runs at most `limit` copies of `fn` in flight
  // at once. Drop-in replacement for `Promise.all(items.map(fn))` when the
  // per-item work is a network call — avoids overwhelming the AWS account's
  // Lambda concurrency cap (currently 10), which manifests as opaque 500s.
  async function mapLimit(items, limit, fn) {
    const out = new Array(items.length);
    let next = 0;
    async function worker() {
      while (true) {
        const i = next++;
        if (i >= items.length) return;
        out[i] = await fn(items[i], i);
      }
    }
    const workers = [];
    for (let w = 0; w < Math.min(limit, items.length); w++) workers.push(worker());
    await Promise.all(workers);
    return out;
  }

  global.SprawlAPI   = SprawlAPI;
  global.SprawlGraph = SprawlGraph;
  global.citizenFull = citizenFull;
  global.mapLimit    = mapLimit;
})(window);
