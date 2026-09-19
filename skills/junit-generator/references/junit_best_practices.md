# JUnit 5 Best Practices for Generated Tests

## Class Naming and Structure

- Use `Test` prefix for test classes: `TestTC001`, `TestPaymentService`
- One test class per component or feature being tested
- Use `@DisplayName` annotation for descriptive test names
- Place test classes under a dedicated source root: `src/test/java`

## Test Method Organization

- Use descriptive method names: `test_userCanLoginWithValidCredentials()`
- Follow the Arrange-Act-Assert (AAA) pattern:
  ```java
  @Test
  public void testPaymentProcessing() {
    // Arrange - Set up test data
    PaymentData payment = new PaymentData(...);
    
    // Act - Execute the code under test
    PaymentResponse response = paymentService.process(payment);
    
    // Assert - Verify the results
    assertEquals("SUCCESS", response.getStatus());
  }
  ```

## Assertions and Validations

- Prefer specific assertions: `assertEquals()`, `assertTrue()`, `assertThrows()`
- One logical assertion per test method (multiple assertions for the same concept are acceptable)
- Use `@DisplayName` on assertions for clarity
- Generate placeholder comments for TODO assertions: `// TODO: Verify user is redirected`

## Test Fixtures and Setup

- Use `@BeforeEach` for setup that runs before each test
- Use `@BeforeAll` for expensive setup (database connections, etc.)
- Keep fixtures in separate classes (e.g., `TestFixtures.java`)
- Reference fixture data by ID for traceability

## Traceability

- Include JavaDoc comments linking to requirements:
  ```java
  /**
   * Auto-generated JUnit test class
   * Generated from test case: TC-001
   * Traceability: REQ-001, REQ-002
   */
  ```
- Tag test classes with `@Tag` for filtering: `@Tag("payment"), @Tag("api")`

## Generated Code Guidelines

- Always include `// TODO:` comments for manual enhancements
- Generate placeholder assertions to ensure code compiles
- Preserve all input data (test case ID, steps, requirements) in the generated code
- Use deterministic file names matching the test case ID

## Dependencies

- JUnit 5 (Jupiter): `org.junit.jupiter:junit-jupiter:5.9.0` or later
- Assertions library: `org.junit.jupiter:junit-jupiter-api`
- Common testing utilities: `org.assertj:assertj-core` (optional, for fluent assertions)

## Anti-Patterns to Avoid

- Do NOT invent test steps that are not in the input
- Do NOT generate assertions without a clear expected value
- Do NOT hardcode configuration values; use fixtures
- Do NOT generate test dependencies between methods
