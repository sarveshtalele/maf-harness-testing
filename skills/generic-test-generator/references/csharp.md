# C#

- Framework: read the test project's `.csproj` `PackageReference` entries: xUnit (`[Fact]`/`[Theory]`), NUnit (`[Test]`/`[TestCase]`) or MSTest (`[TestMethod]`/`[DataRow]`). Never mix frameworks.
- Place the test in the existing test project and mirror the namespace of the class under test; reference the project under test only if the `.csproj` already does.
- Invoke the real public member and assert its result; use `Assert.Throws`/`Assert.ThrowsException` for rejections.
- Mock only declared mocking libraries (Moq, NSubstitute); otherwise hand-written fakes.
- Async tests return `Task` and await; no `Thread.Sleep`.

## Integration tests

- Use `WebApplicationFactory<Program>`; replace services with `ConfigureTestServices` keyed on the exact registered type. Mock only external services; call only mapped routes.

```csharp
var client = new WebApplicationFactory<Program>().CreateClient();
var res = await client.GetAsync("/items");
Assert.Equal(HttpStatusCode.OK, res.StatusCode);
```
