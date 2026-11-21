// The data file checked, and the questions asked of the graph it describes:
// what does a service depend on, what depends on it, and where do the
// dependencies run in a circle.
//
// "A depends on B" is an edge from A to B. No DOM in this file: node tests it
// (sdk/tests/test_example_service_map.py).

export const MAX_SERVICES = 200;
export const TIERS = [1, 2, 3];
const NAME = /^[a-z0-9][a-z0-9._-]{0,62}$/;

// How many single-character edits turn one name into the other.
function editDistance(one, two) {
  let previous = Array.from({ length: two.length + 1 }, (_, index) => index);
  for (let i = 1; i <= one.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= two.length; j += 1) {
      current[j] = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (one[i - 1] === two[j - 1] ? 0 : 1));
    }
    previous = current;
  }
  return previous[two.length];
}

// The known name a typo most likely meant, or "".
export function closestName(wanted, names) {
  let best = "";
  let bestDistance = Math.max(1, Math.floor(wanted.length / 4)) + 1;
  for (const name of names) {
    const distance = editDistance(wanted, name);
    // Close enough, and not a different name altogether ("a" is no typo of "b").
    if (distance < bestDistance && distance < wanted.length) {
      best = name;
      bestDistance = distance;
    }
  }
  return best;
}

function text(value, limit) {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

// Read what services.json holds: {services, problems}.
//
// The map is drawn from whatever is sound, and every entry that is not is
// named in a sentence a person can act on. A data file is edited by hand,
// often in a hurry: "ledgerr is not in the file (did you mean ledger?)" is
// worth more than a blank page.
export function validate(data) {
  const problems = [];
  const list = data && !Array.isArray(data) && typeof data === "object" ? data.services : null;
  if (!Array.isArray(list)) {
    return { services: [], problems: ['The file must be an object with a "services" list: {"services": [{"name": ...}]}.'] };
  }
  if (list.length > MAX_SERVICES) {
    problems.push("The file lists " + list.length + " services; the map draws the first " + MAX_SERVICES + ". Split a larger landscape into several maps.");
  }
  const services = [];
  const names = new Set();
  list.slice(0, MAX_SERVICES).forEach((entry, index) => {
    const where = "Entry " + (index + 1);
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      problems.push(where + " is not an object and was skipped.");
      return;
    }
    const name = text(entry.name, 80);
    if (!NAME.test(name)) {
      problems.push(
        where + (name ? ' is named "' + name + '", which' : " has no name, which") +
          " must be 1-63 characters of a-z, 0-9, dot, dash or underscore, starting with a letter or digit. It was skipped.",
      );
      return;
    }
    if (names.has(name)) {
      problems.push('"' + name + '" appears more than once; only the first entry is used.');
      return;
    }
    names.add(name);
    let tier = entry.tier;
    if (!TIERS.includes(tier)) {
      problems.push('"' + name + '" has the tier ' + JSON.stringify(tier === undefined ? null : tier) + "; a tier is 1, 2 or 3. It is shown as tier 3.");
      tier = 3;
    }
    for (const key of ["links", "depends_on"]) {
      if (entry[key] !== undefined && !Array.isArray(entry[key])) {
        problems.push('"' + name + '" has a "' + key + '" that is not a list and was ignored.');
      }
    }
    const links = [];
    for (const link of Array.isArray(entry.links) ? entry.links.slice(0, 8) : []) {
      if (link && text(link.title, 60) && text(link.url, 500)) {
        links.push({ title: text(link.title, 60), url: text(link.url, 500) });
      } else {
        problems.push('A link of "' + name + '" needs a "title" and a "url" and was skipped.');
      }
    }
    services.push({
      name,
      team: text(entry.team, 40) || "No team",
      tier,
      description: text(entry.description, 400),
      links,
      depends_on: Array.isArray(entry.depends_on) ? entry.depends_on : [],
    });
  });
  for (const service of services) {
    const kept = [];
    for (const target of service.depends_on) {
      if (typeof target !== "string" || !names.has(target)) {
        const guess = typeof target === "string" ? closestName(target, names) : "";
        problems.push(
          '"' + service.name + '" depends on ' + JSON.stringify(target) + ", which is not in the file" + (guess ? ' (did you mean "' + guess + '"?)' : "") + ". The dependency is not drawn.",
        );
      } else if (target === service.name) {
        problems.push('"' + service.name + '" lists itself as a dependency, which is not drawn.');
      } else if (kept.includes(target)) {
        problems.push('"' + service.name + '" lists "' + target + '" twice.');
      } else {
        kept.push(target);
      }
    }
    service.depends_on = kept;
  }
  return { services, problems };
}

// Both directions of every edge: {names, dependsOn, usedBy} with a Map from a name to names.
export function buildIndex(services) {
  const dependsOn = new Map(services.map((service) => [service.name, service.depends_on.slice()]));
  const usedBy = new Map(services.map((service) => [service.name, []]));
  for (const service of services) {
    for (const target of service.depends_on) {
      usedBy.get(target).push(service.name);
    }
  }
  return { names: services.map((service) => service.name), dependsOn, usedBy };
}

// Everything reachable from ``start`` along ``edges``, split into the first
// step ("direct") and everything behind it ("transitive"); both sorted.
// ``start`` itself is in neither, also when a cycle leads back to it.
export function reach(start, edges) {
  const direct = new Set(edges.get(start) || []);
  const seen = new Set([start, ...direct]);
  const todo = Array.from(direct);
  const transitive = [];
  while (todo.length) {
    for (const next of edges.get(todo.pop()) || []) {
      if (!seen.has(next)) {
        seen.add(next);
        transitive.push(next);
        todo.push(next);
      }
    }
  }
  return { direct: Array.from(direct).sort(), transitive: transitive.sort() };
}

// What a service needs (dependencies) and what breaks with it (dependents).
export function neighbourhood(index, name) {
  return { dependencies: reach(name, index.dependsOn), dependents: reach(name, index.usedBy) };
}

// Groups of services that all reach each other: a Map from a name to the
// number of its group. A service outside every cycle is a group of one.
// (Tarjan's algorithm for strongly connected components.)
export function components(index) {
  const found = new Map(); // when a service was first visited
  const lowest = new Map(); // the earliest visited service it can get back to
  const group = new Map();
  const stack = [];
  const onStack = new Set();
  let groups = 0;
  const visit = (name) => {
    found.set(name, found.size);
    lowest.set(name, found.get(name));
    stack.push(name);
    onStack.add(name);
    for (const next of index.dependsOn.get(name) || []) {
      if (!found.has(next)) {
        visit(next);
        lowest.set(name, Math.min(lowest.get(name), lowest.get(next)));
      } else if (onStack.has(next)) {
        lowest.set(name, Math.min(lowest.get(name), found.get(next)));
      }
    }
    if (lowest.get(name) === found.get(name)) {
      let member;
      do {
        member = stack.pop();
        onStack.delete(member);
        group.set(member, groups);
      } while (member !== name);
      groups += 1;
    }
  };
  for (const name of index.names.slice().sort()) {
    if (!found.has(name)) {
      visit(name);
    }
  }
  return group;
}

// A level for every service, for drawing: 0 for what nothing depends on (the
// entry points), and otherwise one more than the deepest service that
// depends on it. The members of a cycle share a level, so the arrows of a
// cycle run sideways and everything else points down.
export function levels(index) {
  const group = components(index);
  const members = new Map();
  for (const [name, number] of group) {
    members.set(number, (members.get(number) || []).concat(name));
  }
  const known = new Map();
  const levelOf = (number) => {
    if (!known.has(number)) {
      let deepest = -1;
      for (const name of members.get(number)) {
        for (const user of index.usedBy.get(name) || []) {
          if (group.get(user) !== number) {
            deepest = Math.max(deepest, levelOf(group.get(user)));
          }
        }
      }
      known.set(number, deepest + 1);
    }
    return known.get(number);
  };
  return new Map(index.names.map((name) => [name, levelOf(group.get(name))]));
}

// Dependency cycles: {cycles, more}. A cycle is a list of names in which
// each depends on the next and the last on the first, written from its
// alphabetically first member, so every cycle is found exactly once.
//
// Each search starts at one service and only walks through services that
// sort after it. That is simple rather than fast, which suits a map of at
// most a few hundred services; ``limit`` and a step budget stop a hopelessly
// tangled file from hanging the page (``more`` is then true).
export function findCycles(index, limit = 20) {
  const names = index.names.slice().sort();
  const order = new Map(names.map((name, position) => [name, position]));
  const cycles = [];
  let budget = 200000;
  let more = false;
  for (const start of names) {
    const path = [start];
    const onPath = new Set(path);
    const walk = (name) => {
      for (const next of index.dependsOn.get(name) || []) {
        if (more) {
          return;
        }
        budget -= 1;
        if (budget < 0 || cycles.length >= limit) {
          more = true;
          return;
        }
        if (next === start) {
          cycles.push(path.slice());
        } else if (order.get(next) > order.get(start) && !onPath.has(next)) {
          path.push(next);
          onPath.add(next);
          walk(next);
          path.pop();
          onPath.delete(next);
        }
      }
    };
    walk(start);
    if (more) {
      break;
    }
  }
  return { cycles, more };
}

// The edges that lie on a cycle, as "from>to" keys, for drawing them differently.
export function cycleEdges(cycles) {
  const keys = new Set();
  for (const cycle of cycles) {
    cycle.forEach((name, position) => keys.add(name + ">" + cycle[(position + 1) % cycle.length]));
  }
  return keys;
}

// Does a service pass the filters? ``query`` matches part of the name, case ignored.
export function matches(service, { team = "", tier = 0, query = "" } = {}) {
  return (!team || service.team === team) && (!tier || service.tier === tier) && service.name.includes(query.trim().toLowerCase());
}
