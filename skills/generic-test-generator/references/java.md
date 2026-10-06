# Java

- Invoke every method claimed by a unit case on its actual source type and assert the observable result. Constructor initialization, comments and imports do not cover setters.
- JUnit version: read `pom.xml` / `build.gradle`. Use `org.junit.Test` + `Assert` for JUnit 4, `org.junit.jupiter.api` for JUnit 5. Never introduce a second JUnit generation or an undeclared library (Mockito, AssertJ, RestAssured...).
- Do not use `@InjectMocks` when the constructor acquires static or external resources (singletons, JNDI, files, sockets). Construct the object explicitly or go through the repository's public API.
- JavaFX: initialise the toolkit first (`new JFXPanel()` or `Platform.startup(...)`) and touch nodes on the FX thread (`Platform.runLater` with a latch).
- Positive fixtures must satisfy source constraints (for example `@Size`); do not assume annotations reject setter input at runtime.
- Parametrize with the declared framework's mechanism only (`@ParameterizedTest` needs JUnit 5 params).
- Place tests in the package of the class under test, under the Maven/Gradle test source root.
