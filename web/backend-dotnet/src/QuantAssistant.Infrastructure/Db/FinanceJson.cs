using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Numerics;
using System.Text.RegularExpressions;

namespace QuantAssistant.Infrastructure.Db;

public static class FinanceJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new DecimalConverter() },
    };
    public sealed class DecimalConverter : JsonConverter<decimal>
    {
        public override decimal Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
        {
            if(reader.TokenType is not (JsonTokenType.String or JsonTokenType.Number))throw new JsonException("Expected a finite decimal value.");
            using var token=JsonDocument.ParseValue(ref reader);
            var raw=(token.RootElement.ValueKind==JsonValueKind.String?token.RootElement.GetString():token.RootElement.GetRawText())?.Trim()??"";
            if(raw.Length>100)throw new JsonException("Decimal precision exceeded.");
            var match=Regex.Match(raw,@"^[+-]?(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$");
            if(!match.Success)throw new JsonException("Expected a finite decimal value.");
            var exponent=0;
            if(match.Groups[3].Success&&(!int.TryParse(match.Groups[3].Value,out exponent)||exponent is < -1000 or >1000))throw new JsonException("Decimal exponent exceeded.");
            var coefficient=BigInteger.Parse(match.Groups[1].Value+match.Groups[2].Value,CultureInfo.InvariantCulture);
            var scale=match.Groups[2].Length-exponent;
            if(coefficient==0)return 0m;
            while(scale>0&&coefficient%10==0){coefficient/=10;scale--;}
            if(scale<0)
            {if(scale< -28)throw new JsonException("Decimal range exceeded.");coefficient*=BigInteger.Pow(10,-scale);scale=0;}
            if(scale>28||coefficient>new BigInteger(decimal.MaxValue))throw new JsonException("Decimal precision exceeded.");
            if(!decimal.TryParse(raw,NumberStyles.Float,CultureInfo.InvariantCulture,out var value))throw new JsonException("Decimal range exceeded.");
            return value;
        }
        public override void Write(Utf8JsonWriter writer, decimal value, JsonSerializerOptions options) => writer.WriteStringValue(value.ToString(CultureInfo.InvariantCulture));
    }
}
