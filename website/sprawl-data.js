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
  const API_URL = window.SPRAWL_API_URL ||
    "https://zujinkdgtj.execute-api.us-east-1.amazonaws.com/dev";
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
    entity:        (id)        => rest(`/entities/${encodeURIComponent(id)}`),
    entities:      (lim=200)   => rest(`/entities?limit=${lim}`),
    entityByType:  (t,lim=50)  => rest(`/entities/by-type/${encodeURIComponent(t)}?limit=${lim}`),
    entityMentions:(id,lim=50) => rest(`/entities/${encodeURIComponent(id)}/mentions?limit=${lim}`),
    arc:           (id)        => rest(`/arcs/${encodeURIComponent(id)}`),
    arcs:          (lim=200)   => rest(`/arcs?limit=${lim}`),
    arcByAnchor:   (l,lim=50)  => rest(`/arcs/by-anchor/${l}?limit=${lim}`),
    arcReferences: (id,lim=50) => rest(`/arcs/${encodeURIComponent(id)}/references?limit=${lim}`),
    votesByLink:   (id,lim=50) => rest(`/votes/by-link/${id}?limit=${lim}`),
    votesByVoter:  (a,lim=50)  => rest(`/votes/by-voter/${a.toLowerCase()}?limit=${lim}`),
    stats:         ()          => rest(`/stats/global`),
    citizenStats:  (a)         => rest(`/citizens/${a.toLowerCase()}/stats`),
    nextLinkId:    ()          => rest(`/next-link-id`),
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
      const { citizens } = await graphql(
        `query($n: Int!) {
          citizens(first: $n, orderBy: registeredAt, orderDirection: desc) {
            id name isBanned registeredAt totalCollectedAsCreator totalSales totalPurchases
          }
        }`, { n: first });
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

  global.SprawlAPI   = SprawlAPI;
  global.SprawlGraph = SprawlGraph;
  global.citizenFull = citizenFull;
})(window);
