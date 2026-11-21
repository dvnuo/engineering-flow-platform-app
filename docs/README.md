# Documentation

| You want to | Read |
|---|---|
| write an app | [DEVELOPING_APPS.md](DEVELOPING_APPS.md): quick start, the manifest, app types and the path prefix, the SDK by topic, local development, limits, troubleshooting |
| turn an existing project into an app | [CONVERTING_A_PROJECT.md](CONVERTING_A_PROJECT.md): the fit check, the procedure, a recipe per framework, the verification list |
| look up an SDK name, a parameter, an error | [SDK_REFERENCE.md](SDK_REFERENCE.md): every module, function, class, environment variable and command |
| test an app without a Portal | `efp_app.testing`, part of the SDK: [SDK_REFERENCE.md](SDK_REFERENCE.md), section 14; a first test is in [DEVELOPING_APPS.md](DEVELOPING_APPS.md#tests-without-a-portal) |
| copy something that works | [the examples](../examples/README.md): complete apps, with an index of which one shows what |
| give the work to an AI agent | [AGENTS.md](../AGENTS.md): what to read, the rules, the commands, what to deliver |
| run the app host | [OPERATIONS.md](OPERATIONS.md): building the image, deploying, the isolation modes, settings, day-to-day operation |
| change the host, the SDK or the Portal's side | [CONTRACTS.md](CONTRACTS.md): every wire format and shared name; change it first, then the code |

The Portal has its own help pages for members: "Using apps" (finding and
opening apps, the app center at the apps address) and "Building apps"
(registering, deploying, the token, audiences, what an app may use).

## The pieces, in one picture

```
 member's browser
       |
       v
 apps address  https://<apps host>/            the app center: the apps you may open
       |       https://<apps host>/<slug>/...  one app
       v
 +--------------------- app host (one container) ----------------------+
 |  gateway: signs visitors in with the Portal, checks who may open    |
 |  what, adds the signed visitor header, serves static apps           |
 |        |                                                            |
 |        v                                                            |
 |  your app: one Python process on 127.0.0.1:$PORT                    |
 |        |  import efp_app                                            |
 +--------|------------------------------------------------------------+
          v
       Portal:  the owner's language model, the owner's connectors
                (Jira, Confluence, GitHub, Jenkins, Nexus, Splunk),
                who owns the app, who may open it, its budget
```

An app is registered and deployed from the Portal: the Portal reads a
directory of a GitHub repository and hands it to the host, which installs
its requirements, starts it, checks that it answers and then serves it.
